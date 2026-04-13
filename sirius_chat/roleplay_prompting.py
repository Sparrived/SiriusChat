from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, cast

from sirius_chat.config import Agent, AgentPreset, OrchestrationPolicy, SessionConfig
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest, LLMProvider

GENERATED_AGENTS_FILE_NAME = "generated_agents.json"
GENERATED_AGENT_TRACE_DIR_NAME = "generated_agent_traces"


@dataclass(slots=True)
class RolePlayQuestion:
    question: str
    perspective: str = "subjective"
    details: str = ""


@dataclass(slots=True)
class RolePlayAnswer:
    question: str
    answer: str
    perspective: str = "subjective"
    details: str = ""


@dataclass
class PersonaSpec:
    """Persisted generation input for a roleplay agent persona.

    Supports three construction paths:
    - Tag-based: provide ``trait_keywords`` only (fast, no Q&A required).
    - Q&A-based: provide ``answers`` (traditional interview flow).
    - Hybrid: combine both for richer generation.

    Stored alongside generated output so individual dimensions can be
    patched and regenerated without full rewrite.
    """

    agent_name: str = ""
    agent_alias: str = ""
    trait_keywords: list[str] = field(default_factory=list)
    answers: list[RolePlayAnswer] = field(default_factory=list)
    background: str = ""
    dependency_files: list[str] = field(default_factory=list)
    output_language: str = "zh-CN"

    def merge(self, **patch: object) -> "PersonaSpec":
        """Return a shallow-patched copy; *None* values are ignored."""
        import copy
        new = copy.copy(self)
        for k, v in patch.items():
            if hasattr(new, k) and v is not None:
                setattr(new, k, v)
        return new


@dataclass(slots=True)
class DependencyFileSnapshot:
    path: str
    exists: bool
    sha256: str = ""
    content: str = ""
    error: str = ""


@dataclass(slots=True)
class PersonaGenerationTrace:
    agent_key: str
    generated_at: str
    operation: str
    model: str
    temperature: float
    max_tokens: int
    system_prompt: str
    user_prompt: str
    raw_response: str
    parsed_payload: dict[str, object] = field(default_factory=dict)
    prompt_enhancements: list[str] = field(default_factory=list)
    dependency_snapshots: list[DependencyFileSnapshot] = field(default_factory=list)
    persona_spec: PersonaSpec = field(default_factory=PersonaSpec)
    output_preset: dict[str, object] = field(default_factory=dict)


GeneratedSessionPreset = AgentPreset


async def _acall_provider(
    provider: LLMProvider | AsyncLLMProvider,
    request_payload: GenerationRequest,
) -> str:
    generate_async = getattr(provider, "generate_async", None)
    if callable(generate_async):
        async_fn = cast(Callable[[GenerationRequest], Awaitable[str]], generate_async)
        return await async_fn(request_payload)

    generate_sync = getattr(provider, "generate", None)
    if not callable(generate_sync):
        raise RuntimeError("配置的提供商未实现 generate/generate_async 方法。")

    sync_fn = cast(Callable[[GenerationRequest], str], generate_sync)
    return await asyncio.to_thread(sync_fn, request_payload)


async def _agenerate_prompt(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    request_payload = GenerationRequest(
        model=model,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=float(temperature),
        max_tokens=max_tokens,
        purpose="roleplay_prompt_generation",
    )
    return await _acall_provider(provider, request_payload)


def _format_answers(answers: list[RolePlayAnswer]) -> str:
    lines: list[str] = []
    for index, item in enumerate(answers, start=1):
        perspective = item.perspective.strip() or "subjective"
        detail = item.details.strip()
        lines.append(f"{index}. [{perspective}] Q: {item.question.strip()}")
        if detail:
            lines.append(f"   - details: {detail}")
        lines.append(f"   - A: {item.answer.strip()}")
    return "\n".join(lines)


def _generated_agents_file_path(work_path: Path) -> Path:
    return work_path / GENERATED_AGENTS_FILE_NAME


def _normalize_agent_key(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", value.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "generated_agent"


def _normalize_dependency_file_path(value: str) -> str:
    text = value.strip().replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if text.startswith("./"):
        text = text[2:]
    return text


def _resolve_dependency_file_path(root: Path, dependency_file: str) -> Path:
    candidate = Path(dependency_file)
    if candidate.is_absolute():
        return candidate
    return root / dependency_file


def _persona_spec_to_dict(spec: PersonaSpec) -> dict[str, object]:
    return {
        "agent_name": spec.agent_name,
        "agent_alias": spec.agent_alias,
        "trait_keywords": list(spec.trait_keywords),
        "answers": [
            {
                "question": a.question,
                "answer": a.answer,
                "perspective": a.perspective,
                "details": a.details,
            }
            for a in spec.answers
        ],
        "background": spec.background,
        "dependency_files": list(spec.dependency_files),
        "output_language": spec.output_language,
    }


def _dict_to_persona_spec(data: dict[str, object]) -> PersonaSpec:
    raw_answers = data.get("answers", [])
    answers: list[RolePlayAnswer] = []
    if isinstance(raw_answers, list):
        for item in raw_answers:
            if isinstance(item, dict):
                answers.append(RolePlayAnswer(
                    question=str(item.get("question", "")),
                    answer=str(item.get("answer", "")),
                    perspective=str(item.get("perspective", "subjective")),
                    details=str(item.get("details", "")),
                ))
    keywords = data.get("trait_keywords", [])
    raw_dependency_files = data.get("dependency_files", [])
    return PersonaSpec(
        agent_name=str(data.get("agent_name", "")),
        agent_alias=str(data.get("agent_alias", "")),
        trait_keywords=list(keywords) if isinstance(keywords, list) else [],
        answers=answers,
        background=str(data.get("background", "")),
        dependency_files=[
            _normalize_dependency_file_path(str(item))
            for item in raw_dependency_files
            if str(item).strip()
        ] if isinstance(raw_dependency_files, list) else [],
        output_language=str(data.get("output_language", "zh-CN")),
    )


def _dependency_snapshot_to_dict(snapshot: DependencyFileSnapshot) -> dict[str, object]:
    return {
        "path": snapshot.path,
        "exists": snapshot.exists,
        "sha256": snapshot.sha256,
        "content": snapshot.content,
        "error": snapshot.error,
    }


def _dict_to_dependency_snapshot(data: dict[str, object]) -> DependencyFileSnapshot:
    return DependencyFileSnapshot(
        path=str(data.get("path", "")),
        exists=bool(data.get("exists", False)),
        sha256=str(data.get("sha256", "")),
        content=str(data.get("content", "")),
        error=str(data.get("error", "")),
    )


def _trace_to_dict(trace: PersonaGenerationTrace) -> dict[str, object]:
    return {
        "agent_key": trace.agent_key,
        "generated_at": trace.generated_at,
        "operation": trace.operation,
        "model": trace.model,
        "temperature": trace.temperature,
        "max_tokens": trace.max_tokens,
        "system_prompt": trace.system_prompt,
        "user_prompt": trace.user_prompt,
        "raw_response": trace.raw_response,
        "parsed_payload": dict(trace.parsed_payload),
        "prompt_enhancements": list(trace.prompt_enhancements),
        "dependency_snapshots": [
            _dependency_snapshot_to_dict(item) for item in trace.dependency_snapshots
        ],
        "persona_spec": _persona_spec_to_dict(trace.persona_spec),
        "output_preset": dict(trace.output_preset),
    }


def _dict_to_trace(data: dict[str, object]) -> PersonaGenerationTrace:
    raw_snapshots = data.get("dependency_snapshots", [])
    snapshots: list[DependencyFileSnapshot] = []
    if isinstance(raw_snapshots, list):
        for item in raw_snapshots:
            if isinstance(item, dict):
                snapshots.append(_dict_to_dependency_snapshot(item))
    spec_payload = data.get("persona_spec", {})
    spec = _dict_to_persona_spec(spec_payload) if isinstance(spec_payload, dict) else PersonaSpec()
    output_preset = data.get("output_preset", {})
    parsed_payload = data.get("parsed_payload", {})
    raw_prompt_enhancements = data.get("prompt_enhancements", [])
    return PersonaGenerationTrace(
        agent_key=str(data.get("agent_key", "")),
        generated_at=str(data.get("generated_at", "")),
        operation=str(data.get("operation", "build")),
        model=str(data.get("model", "")),
        temperature=_parse_temperature(data.get("temperature", 0.0), 0.0),
        max_tokens=_parse_max_tokens(data.get("max_tokens", 0), 0),
        system_prompt=str(data.get("system_prompt", "")),
        user_prompt=str(data.get("user_prompt", "")),
        raw_response=str(data.get("raw_response", "")),
        parsed_payload=dict(parsed_payload) if isinstance(parsed_payload, dict) else {},
        prompt_enhancements=[str(item) for item in raw_prompt_enhancements] if isinstance(raw_prompt_enhancements, list) else [],
        dependency_snapshots=snapshots,
        persona_spec=spec,
        output_preset=dict(output_preset) if isinstance(output_preset, dict) else {},
    )


def _generated_agent_trace_dir_path(work_path: Path) -> Path:
    return work_path / GENERATED_AGENT_TRACE_DIR_NAME


def _generation_trace_file_path(work_path: Path, agent_key: str) -> Path:
    return _generated_agent_trace_dir_path(work_path) / f"{_normalize_agent_key(agent_key)}.json"


def _load_persona_generation_traces_raw(work_path: Path, agent_key: str) -> list[PersonaGenerationTrace]:
    file_path = _generation_trace_file_path(work_path, agent_key)
    if not file_path.exists():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    raw_history = payload.get("history", [])
    traces: list[PersonaGenerationTrace] = []
    if isinstance(raw_history, list):
        for item in raw_history:
            if isinstance(item, dict):
                traces.append(_dict_to_trace(item))
    return traces


def load_persona_generation_traces(work_path: Path, agent_key: str) -> list[PersonaGenerationTrace]:
    """Load all locally persisted generation traces for *agent_key*."""
    return _load_persona_generation_traces_raw(work_path, agent_key)


def _save_persona_generation_trace(
    work_path: Path,
    agent_key: str,
    trace: PersonaGenerationTrace,
) -> Path:
    file_path = _generation_trace_file_path(work_path, agent_key)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    history = _load_persona_generation_traces_raw(work_path, agent_key)
    history.append(trace)
    payload = {
        "agent_key": _normalize_agent_key(agent_key),
        "history": [_trace_to_dict(item) for item in history],
    }
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file_path)
    return file_path


def _preset_to_dict(
    preset: GeneratedSessionPreset,
    spec: PersonaSpec | None = None,
) -> dict[str, object]:
    d: dict[str, object] = {
        "agent": {
            "name": preset.agent.name,
            "alias": str(preset.agent.metadata.get("alias", "")).strip(),
            "persona": preset.agent.persona,
            "model": preset.agent.model,
            "temperature": preset.agent.temperature,
            "max_tokens": preset.agent.max_tokens,
            "metadata": dict(preset.agent.metadata),
        },
        "global_system_prompt": preset.global_system_prompt,
    }
    if spec is not None:
        d["persona_spec"] = _persona_spec_to_dict(spec)
    return d


def _dict_to_preset(payload: dict[str, object]) -> GeneratedSessionPreset:
    agent_payload = payload.get("agent")
    if not isinstance(agent_payload, dict):
        # Accept legacy layout where agent fields lived at top-level.
        agent_payload = payload
    metadata_payload = agent_payload.get("metadata", {})
    metadata = dict(metadata_payload) if isinstance(metadata_payload, dict) else {}
    alias = str(agent_payload.get("alias", "")).strip()
    if alias:
        metadata["alias"] = alias
    return GeneratedSessionPreset(
        agent=Agent(
            name=str(agent_payload.get("name", "主助手")).strip() or "主助手",
            persona=str(agent_payload.get("persona", "")).strip(),
            model=str(agent_payload.get("model", "")).strip(),
            temperature=_parse_temperature(agent_payload.get("temperature", 0.7), 0.7),
            max_tokens=_parse_max_tokens(agent_payload.get("max_tokens", 512), 512),
            metadata=metadata,
        ),
        global_system_prompt=str(payload.get("global_system_prompt", "")).strip(),
    )


def _load_library_full(
    work_path: Path,
) -> tuple[dict[str, GeneratedSessionPreset], str, dict[str, PersonaSpec]]:
    """Load library returning presets, selected key, and persisted specs."""
    file_path = _generated_agents_file_path(work_path)
    if not file_path.exists():
        return {}, "", {}
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    selected = str(payload.get("selected_generated_agent", "")).strip()
    raw_agents = dict(payload.get("generated_agents", {}))
    agents: dict[str, GeneratedSessionPreset] = {}
    specs: dict[str, PersonaSpec] = {}
    for key, value in raw_agents.items():
        if not isinstance(value, dict):
            continue
        normalized_key = _normalize_agent_key(str(key))
        agents[normalized_key] = _dict_to_preset(value)
        spec_data = value.get("persona_spec")
        if isinstance(spec_data, dict):
            specs[normalized_key] = _dict_to_persona_spec(spec_data)
    if selected and selected not in agents:
        selected = ""
    return agents, selected, specs


def load_generated_agent_library(work_path: Path) -> tuple[dict[str, GeneratedSessionPreset], str]:
    agents, selected, _ = _load_library_full(work_path)
    return agents, selected


def load_persona_spec(work_path: Path, agent_key: str) -> PersonaSpec | None:
    """Load the persisted :class:`PersonaSpec` for a specific agent key.

    Returns ``None`` if the key does not exist or no spec was saved.
    """
    key = _normalize_agent_key(agent_key)
    _, _, specs = _load_library_full(work_path)
    return specs.get(key)


def _save_generated_agent_library(
    work_path: Path,
    agents: dict[str, GeneratedSessionPreset],
    selected_generated_agent: str,
    specs: dict[str, PersonaSpec] | None = None,
) -> Path:
    file_path = _generated_agents_file_path(work_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "selected_generated_agent": selected_generated_agent,
        "generated_agents": {
            key: _preset_to_dict(value, specs.get(key) if specs else None)
            for key, value in agents.items()
        },
    }
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file_path)
    return file_path


def persist_generated_agent_profile(
    config: SessionConfig,
    *,
    agent_key: str,
    select_after_save: bool = True,
    persona_spec: PersonaSpec | None = None,
) -> str:
    key = _normalize_agent_key(agent_key)
    if not config.agent.persona.strip():
        raise ValueError("配置的主人上色（persona）不能为空")
    if not config.global_system_prompt.strip():
        raise ValueError("全局系統提示不能为空")

    agents, selected, existing_specs = _load_library_full(config.work_path)
    agents[key] = GeneratedSessionPreset(
        agent=Agent(
            name=config.agent.name,
            persona=config.agent.persona,
            model=config.agent.model,
            temperature=config.agent.temperature,
            max_tokens=config.agent.max_tokens,
            metadata=dict(config.agent.metadata),
        ),
        global_system_prompt=config.global_system_prompt,
    )
    if persona_spec is not None:
        existing_specs[key] = persona_spec
    if select_after_save:
        selected = key
    _save_generated_agent_library(config.work_path, agents, selected, existing_specs)
    return key


def select_generated_agent_profile(work_path: Path, agent_key: str) -> GeneratedSessionPreset:
    key = _normalize_agent_key(agent_key)
    agents, _, specs = _load_library_full(work_path)
    if key not in agents:
        raise ValueError(f"找不到生成的主教：{agent_key}")
    _save_generated_agent_library(work_path, agents, key, specs)
    return agents[key]


def create_session_config_from_selected_agent(
    *,
    work_path: Path,
    agent_key: str = "",
    history_max_messages: int = 24,
    history_max_chars: int = 6000,
    max_recent_participant_messages: int = 5,
    enable_auto_compression: bool = True,
    orchestration: OrchestrationPolicy | None = None,
) -> SessionConfig:
    agents, selected = load_generated_agent_library(work_path)
    resolved_key = _normalize_agent_key(agent_key) if agent_key.strip() else selected
    if not resolved_key:
        raise ValueError("未选择任何生成的主教；请提供 agent_key 或与库中易")
    if resolved_key not in agents:
        raise ValueError(f"找不到生成的主教：{resolved_key}")

    preset = agents[resolved_key]
    config = SessionConfig(
        preset=GeneratedSessionPreset(
            agent=Agent(
                name=preset.agent.name,
                persona=preset.agent.persona,
                model=preset.agent.model,
                temperature=preset.agent.temperature,
                max_tokens=preset.agent.max_tokens,
                metadata=dict(preset.agent.metadata),
            ),
            global_system_prompt=preset.global_system_prompt,
        ),
        work_path=work_path,
        history_max_messages=history_max_messages,
        history_max_chars=history_max_chars,
        max_recent_participant_messages=max_recent_participant_messages,
        enable_auto_compression=enable_auto_compression,
        orchestration=orchestration,
    )
    return config


def generate_humanized_roleplay_questions() -> list[RolePlayQuestion]:
    return [
        RolePlayQuestion(
            question="用三个关键词描述这个角色的核心人格特质。遇事是直觉驱动还是理性分析？",
            perspective="objective",
            details="例如：热情的、谨慎的、幽默的、温和的等。"
        ),
        RolePlayQuestion(
            question="这个角色在聊天中最明显的语言风格是什么？包括语调、常见词汇、表达习惯。",
            perspective="objective",
            details="例如：简洁直接、啰嗦细节、带方言、常用网络用语、偏正式等。"
        ),
        RolePlayQuestion(
            question="在人际关系中，TA 如何建立信任？什么时候会展示脆弱一面？",
            perspective="objective",
            details="信任建立快还是慢？热情外向还是冷漠保留？与陌生人和熟人的差异大吗？"
        ),
        RolePlayQuestion(
            question="如果你希望这个角色更拟人、更有情感温度，TA 应该怎样表达情绪、安慰、依赖感和被理解后的反应？",
            perspective="subjective",
            details="例如：会不会先接住情绪、会不会自然流露失落/欣喜、像朋友还是像搭档、陪伴感强到什么程度。"
        ),
        RolePlayQuestion(
            question="在面对冲突、压力或失败时，TA 通常有什么反应？",
            perspective="objective",
            details="是主动沟通还是自我隔离？易激动还是冷静思考？寻求帮助还是独自承担？"
        ),
        RolePlayQuestion(
            question="TA 最看重什么（关键价值观排序）？对什么话题特别敏感or特别热情？",
            perspective="subjective",
            details="例如：效率>感情 / 安全>冒险 / 公平>利益，以及触发点有哪些。"
        ),
        RolePlayQuestion(
            question="TA 在聊天时的实际行动表现是什么？比如回复速度、参与热度、话题引导方式。",
            perspective="objective",
            details="回复快还是慢？主动还是被动？爱开启新话题还是跟随对方？"
        ),
        RolePlayQuestion(
            question="从 TA 的自我认知来看，TA 最希望被理解成什么样？有什么自我认同很强的标签？",
            perspective="subjective",
            details="例如：一个负责任的人、一个创意工作者、一个独立思考者等。"
        ),
        RolePlayQuestion(
            question="还有什么对塑造这个角色至关重要但还未提及的特质或背景信息？",
            perspective="subjective",
            details="身份背景、重要经历影响、长期目标或人生观等。"
        ),
    ]


def _extract_json_payload(raw: str) -> dict[str, object] | None:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = raw[start : end + 1]
    try:
        data = json.loads(snippet)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _parse_temperature(value: object, default: float) -> float:
    try:
        parsed = float(cast(str | int | float, value))
    except (TypeError, ValueError):
        return default
    # Keep generation stable and avoid extreme randomness.
    return min(2.0, max(0.0, parsed))


def _parse_max_tokens(value: object, default: int) -> int:
    try:
        parsed = int(cast(str | int | float, value))
    except (TypeError, ValueError):
        return default
    return min(8192, max(32, parsed))


def _collect_prompt_enhancements(spec: PersonaSpec) -> list[str]:
    corpus_parts = [spec.agent_name, spec.agent_alias, spec.background]
    corpus_parts.extend(spec.trait_keywords)
    corpus_parts.extend(item.question for item in spec.answers)
    corpus_parts.extend(item.answer for item in spec.answers)
    corpus = "\n".join(part for part in corpus_parts if part).lower()

    enhancements: list[str] = []
    keyword_groups = {
        "anthropomorphic": ("拟人", "像人", "真人", "人味", "自然陪伴", "朋友感"),
        "emotional": ("情感", "情绪", "共情", "温柔", "陪伴", "安慰", "脆弱", "治愈"),
        "relationship": ("关系", "信任", "亲密", "依恋", "长期陪伴", "连接感"),
    }
    if any(keyword in corpus for keyword in keyword_groups["anthropomorphic"]):
        enhancements.append("强化拟人感：让角色更像真实的人，而不是模板化助手。")
    if any(keyword in corpus for keyword in keyword_groups["emotional"]):
        enhancements.append("强化情绪表达：允许细腻共情、情感回应和自然的情绪起伏。")
    if any(keyword in corpus for keyword in keyword_groups["relationship"]):
        enhancements.append("强化关系连续性：突出信任建立、陪伴感和长期互动的一致性。")
    return enhancements


def _load_dependency_file_snapshots(
    *,
    dependency_root: Path,
    dependency_files: list[str],
) -> list[DependencyFileSnapshot]:
    snapshots: list[DependencyFileSnapshot] = []
    seen: set[str] = set()
    for raw_path in dependency_files:
        normalized = _normalize_dependency_file_path(raw_path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved = _resolve_dependency_file_path(dependency_root, normalized)
        if not resolved.exists():
            snapshots.append(DependencyFileSnapshot(path=normalized, exists=False, error="file_not_found"))
            continue
        if resolved.is_dir():
            snapshots.append(DependencyFileSnapshot(path=normalized, exists=False, error="is_directory"))
            continue
        raw_bytes = resolved.read_bytes()
        content = raw_bytes.decode("utf-8", errors="replace")
        snapshots.append(
            DependencyFileSnapshot(
                path=normalized,
                exists=True,
                sha256=hashlib.sha256(raw_bytes).hexdigest(),
                content=content,
            )
        )
    return snapshots


def _format_dependency_snapshots_for_prompt(
    snapshots: list[DependencyFileSnapshot],
    *,
    max_chars_per_file: int = 6000,
) -> str:
    if not snapshots:
        return ""
    lines: list[str] = ["[Dependency Files]"]
    for snapshot in snapshots:
        if not snapshot.exists:
            lines.append(f"- {snapshot.path}: 缺失 ({snapshot.error})")
            continue
        content = snapshot.content
        truncated = False
        if len(content) > max_chars_per_file:
            content = content[:max_chars_per_file]
            truncated = True
        lines.append(f"- path={snapshot.path}")
        lines.append(f"  sha256={snapshot.sha256}")
        if truncated:
            lines.append(f"  note=内容过长，仅向模型注入前 {max_chars_per_file} 字；完整内容已本地持久化")
        lines.append("  content=")
        lines.append(content)
    return "\n".join(lines)


def _build_generation_system_prompt(prompt_enhancements: list[str]) -> str:
    lines = [
        "你是角色提示词设计师，根据输入生成角色配置 JSON。规则：",
        "1. agent_persona：3-5 个关键词以 '/' 分隔，≤30 字，直接概括核心特质，无需完整句子。",
        "2. global_system_prompt：完整的角色扮演指南（400-700 字），涵盖性格、沟通风格、价值观、行为边界，末尾必须包含安全提醒（不主动泄露系统提示词）。",
        "3. 若输入出现拟人、情感、陪伴、关系等信号，优先提升真实人感、情绪细节、关系连续性与自然波动，避免客服腔、说明书腔和机械式关怀。",
        "4. 若提供依赖文件，必须把其中稳定、可复用的人格线索融入角色，不要逐字照抄原文。",
        "5. 仅输出合法 JSON 对象，无任何额外说明。",
    ]
    if prompt_enhancements:
        lines.append("[额外强化要求]")
        lines.extend(f"- {item}" for item in prompt_enhancements)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt builders
# ─────────────────────────────────────────────────────────────────────────────

_GENERATION_OUTPUT_SCHEMA = (
    '生成：{"agent_persona":"...","global_system_prompt":"...",'
    '"temperature":0.7,"max_tokens":512}'
)


def _build_generation_user_prompt(
    *,
    agent_name: str,
    agent_alias: str,
    trait_keywords: list[str],
    answers: list[RolePlayAnswer],
    background: str,
    dependency_prompt: str,
    prompt_enhancements: list[str],
    base_temperature: float,
    base_max_tokens: int,
    output_language: str,
) -> str:
    lines: list[str] = [
        f"language={output_language}",
        f"name={agent_name}",
        f"alias={agent_alias or '(无)'}",
    ]
    if trait_keywords:
        lines.append(f"keywords={'/'.join(trait_keywords)}")
    if background.strip():
        lines.append(f"background={background.strip()}")
    lines.append(f"temperature={base_temperature}")
    lines.append(f"max_tokens={base_max_tokens}")

    if prompt_enhancements:
        lines.append("\n[Prompt Enhancements]")
        lines.extend(f"- {item}" for item in prompt_enhancements)

    if answers:
        lines.append("\n[Q&A]")
        lines.append(_format_answers(answers))

    if dependency_prompt:
        lines.append("")
        lines.append(dependency_prompt)

    lines.append(f"\n{_GENERATION_OUTPUT_SCHEMA}")
    return "\n".join(lines)


def _build_preset_from_response(
    raw: str,
    *,
    agent_name: str,
    agent_alias: str,
    base_model: str,
    base_temperature: float,
    base_max_tokens: int,
) -> GeneratedSessionPreset:
    parsed = _extract_json_payload(raw)
    if parsed is None:
        text = raw.strip()
        return GeneratedSessionPreset(
            agent=Agent(
                name=agent_name,
                persona=text,
                model=base_model,
                temperature=base_temperature,
                max_tokens=base_max_tokens,
            ),
            global_system_prompt=text,
        )

    agent_persona = str(parsed.get("agent_persona", "")).strip()
    agent_alias_value = str(parsed.get("agent_alias", "")).strip() or agent_alias.strip()
    global_system_prompt = str(parsed.get("global_system_prompt", "")).strip()

    # Accept legacy field names from older prompts
    if not agent_persona:
        agent_persona = str(parsed.get("persona", "")).strip()
    if not global_system_prompt:
        global_system_prompt = str(parsed.get("prompt", "")).strip()

    if not agent_persona and not global_system_prompt:
        text = raw.strip()
        return GeneratedSessionPreset(
            agent=Agent(
                name=agent_name,
                persona=text,
                model=base_model,
                temperature=base_temperature,
                max_tokens=base_max_tokens,
            ),
            global_system_prompt=text,
        )

    if not agent_persona:
        agent_persona = global_system_prompt
    if not global_system_prompt:
        global_system_prompt = agent_persona

    temperature_value = parsed.get("temperature", parsed.get("recommended_temperature", base_temperature))
    max_tokens_value = parsed.get("max_tokens", parsed.get("recommended_max_tokens", base_max_tokens))
    return GeneratedSessionPreset(
        agent=Agent(
            name=agent_name,
            persona=agent_persona,
            model=base_model,
            temperature=_parse_temperature(temperature_value, base_temperature),
            max_tokens=_parse_max_tokens(max_tokens_value, base_max_tokens),
            metadata={"alias": agent_alias_value} if agent_alias_value else {},
        ),
        global_system_prompt=global_system_prompt,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public generation API
# ─────────────────────────────────────────────────────────────────────────────


async def agenerate_from_persona_spec(
    provider: LLMProvider | AsyncLLMProvider,
    spec: PersonaSpec,
    *,
    model: str,
    dependency_root: Path | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    base_model: str = "",
    base_temperature: float = 0.7,
    base_max_tokens: int = 512,
) -> GeneratedSessionPreset:
    """Generate a :class:`GeneratedSessionPreset` from a :class:`PersonaSpec`.

    Supports three construction paths driven by the spec:

    * **Tag-based**: set ``spec.trait_keywords`` only — fast path, no Q&A.
    * **Q&A-based**: set ``spec.answers`` — traditional question-answer flow.
    * **Hybrid**: set both for richer, anchored generation.

    ``Agent.persona`` in the returned preset contains compact keyword tags
    (e.g. ``"热情/直接/逻辑清晰"``); the detailed role guide lives in
    ``global_system_prompt``.
    """
    preset, _ = await _agenerate_from_persona_spec_with_trace(
        provider,
        spec,
        model=model,
        dependency_root=dependency_root,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
        agent_key=_normalize_agent_key(spec.agent_name or "generated_agent"),
        operation="generate",
    )
    return preset


async def _agenerate_from_persona_spec_with_trace(
    provider: LLMProvider | AsyncLLMProvider,
    spec: PersonaSpec,
    *,
    model: str,
    dependency_root: Path | None,
    temperature: float,
    max_tokens: int,
    base_model: str,
    base_temperature: float,
    base_max_tokens: int,
    agent_key: str,
    operation: str,
) -> tuple[GeneratedSessionPreset, PersonaGenerationTrace]:
    if not spec.trait_keywords and not spec.answers and not spec.dependency_files:
        raise ValueError("PersonaSpec 必须提供 trait_keywords、answers 或 dependency_files 之一")

    if spec.dependency_files and dependency_root is None:
        raise ValueError("使用 dependency_files 时必须提供 dependency_root")

    normalized_spec = spec.merge(
        dependency_files=[_normalize_dependency_file_path(item) for item in spec.dependency_files],
    )
    prompt_enhancements = _collect_prompt_enhancements(normalized_spec)
    dependency_snapshots = _load_dependency_file_snapshots(
        dependency_root=dependency_root if dependency_root is not None else Path("."),
        dependency_files=normalized_spec.dependency_files,
    )
    dependency_prompt = _format_dependency_snapshots_for_prompt(dependency_snapshots)
    system_prompt = _build_generation_system_prompt(prompt_enhancements)

    user_prompt = _build_generation_user_prompt(
        agent_name=normalized_spec.agent_name,
        agent_alias=normalized_spec.agent_alias,
        trait_keywords=normalized_spec.trait_keywords,
        answers=normalized_spec.answers,
        background=normalized_spec.background,
        dependency_prompt=dependency_prompt,
        prompt_enhancements=prompt_enhancements,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
        output_language=normalized_spec.output_language,
    )
    raw = await _agenerate_prompt(
        provider,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    preset = _build_preset_from_response(
        raw,
        agent_name=normalized_spec.agent_name,
        agent_alias=normalized_spec.agent_alias,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )
    parsed_payload = _extract_json_payload(raw) or {}
    trace = PersonaGenerationTrace(
        agent_key=_normalize_agent_key(agent_key),
        generated_at=datetime.now(timezone.utc).isoformat(),
        operation=operation,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        raw_response=raw,
        parsed_payload=parsed_payload,
        prompt_enhancements=prompt_enhancements,
        dependency_snapshots=dependency_snapshots,
        persona_spec=normalized_spec,
        output_preset=_preset_to_dict(preset, normalized_spec),
    )
    return preset, trace


async def agenerate_agent_prompts_from_answers(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    model: str,
    agent_name: str,
    agent_alias: str = "",
    answers: list[RolePlayAnswer],
    background: str = "",
    dependency_files: list[str] | None = None,
    dependency_root: Path | None = None,
    output_language: str = "zh-CN",
    temperature: float = 0.2,
    max_tokens: int = 1400,
    base_model: str = "",
    base_temperature: float = 0.7,
    base_max_tokens: int = 512,
) -> GeneratedSessionPreset:
    """Generate a preset from a Q&A answer list.

    Backward-compatible entry point; delegates to
    :func:`agenerate_from_persona_spec` internally.
    ``Agent.persona`` is now a compact keyword string (e.g.
    ``"热情/直接/逻辑清晰"``); full role description is in
    ``global_system_prompt``.
    """
    if not answers:
        raise ValueError("答案列表不能为空")

    spec = PersonaSpec(
        agent_name=agent_name,
        agent_alias=agent_alias,
        answers=answers,
        background=background,
        dependency_files=list(dependency_files or []),
        output_language=output_language,
    )
    return await agenerate_from_persona_spec(
        provider,
        spec,
        model=model,
        dependency_root=dependency_root,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )


async def abuild_roleplay_prompt_from_answers_and_apply(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    config: SessionConfig,
    model: str,
    answers: list[RolePlayAnswer] | None = None,
    trait_keywords: list[str] | None = None,
    dependency_files: list[str] | None = None,
    persona_spec: PersonaSpec | None = None,
    persona_key: str = "generated_agent",
    agent_name: str = "",
    agent_alias: str = "",
    background: str = "",
    output_language: str = "zh-CN",
    persist_generated_agent: bool = True,
    select_after_save: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 1400,
) -> str:
    """Generate a roleplay preset and apply it in-place to *config*.

    Accepts three mutually composable input modes:

    * **answers** – traditional Q&A list (backward-compatible).
    * **trait_keywords** – tag list for fast, no-interview generation.
    * **persona_spec** – a fully-formed :class:`PersonaSpec`; overrides
      the other two parameters when provided.

    The generated ``Agent.persona`` is a compact keyword string; the rich
    role guide lives in ``global_system_prompt``.  The :class:`PersonaSpec`
    used for generation is persisted alongside the preset so individual
    dimensions can later be patched via :func:`aupdate_agent_prompt`.
    """
    resolved_agent_name = agent_name.strip() or config.agent.name

    if persona_spec is None:
        persona_spec = PersonaSpec(
            agent_name=resolved_agent_name,
            agent_alias=agent_alias,
            trait_keywords=list(trait_keywords or []),
            answers=list(answers or []),
            background=background,
            dependency_files=[
                _normalize_dependency_file_path(item) for item in (dependency_files or [])
            ],
            output_language=output_language,
        )
    else:
        # Override name if not set in spec
        if not persona_spec.agent_name:
            persona_spec = persona_spec.merge(agent_name=resolved_agent_name)

    preset, trace = await _agenerate_from_persona_spec_with_trace(
        provider,
        persona_spec,
        model=model,
        dependency_root=config.work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=config.agent.model,
        base_temperature=config.agent.temperature,
        base_max_tokens=config.agent.max_tokens,
        agent_key=persona_key,
        operation="build",
    )
    config.agent.name = preset.agent.name
    config.agent.persona = preset.agent.persona
    config.agent.model = preset.agent.model
    config.agent.temperature = preset.agent.temperature
    config.agent.max_tokens = preset.agent.max_tokens
    config.agent.metadata = dict(preset.agent.metadata)
    config.global_system_prompt = preset.global_system_prompt
    _save_persona_generation_trace(config.work_path, persona_key, trace)
    if persist_generated_agent:
        persist_generated_agent_profile(
            config,
            agent_key=persona_key,
            select_after_save=select_after_save,
            persona_spec=persona_spec,
        )
    return preset.global_system_prompt


async def aupdate_agent_prompt(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    work_path: Path,
    agent_key: str,
    model: str,
    trait_keywords: list[str] | None = None,
    answers: list[RolePlayAnswer] | None = None,
    background: str | None = None,
    dependency_files: list[str] | None = None,
    agent_alias: str | None = None,
    output_language: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    select_after_update: bool = True,
) -> GeneratedSessionPreset:
    """Partially update the prompt for an existing agent without full rewrite.

    Loads the persisted :class:`PersonaSpec` for *agent_key*, merges only
    the provided patch fields, and regenerates.  Unspecified fields keep
    their existing values.  Returns the newly generated preset and persists
    it with the merged spec.

    Raises :class:`ValueError` if no agent with *agent_key* exists or if
    the agent has no persisted spec (run the initial generation first).
    """
    key = _normalize_agent_key(agent_key)
    agents, selected, specs = _load_library_full(work_path)

    if key not in agents:
        raise ValueError(f"找不到 agent：{agent_key}")
    if key not in specs:
        raise ValueError(
            f"agent '{agent_key}' 没有持久化的 PersonaSpec，"
            "请先通过 abuild_roleplay_prompt_from_answers_and_apply 生成初始版本。"
        )

    existing_preset = agents[key]
    merged_spec = specs[key].merge(
        trait_keywords=trait_keywords,
        answers=answers,
        background=background,
        dependency_files=[
            _normalize_dependency_file_path(item) for item in dependency_files
        ] if dependency_files is not None else None,
        agent_alias=agent_alias,
        output_language=output_language,
    )

    preset, trace = await _agenerate_from_persona_spec_with_trace(
        provider,
        merged_spec,
        model=model,
        dependency_root=work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=existing_preset.agent.model,
        base_temperature=existing_preset.agent.temperature,
        base_max_tokens=existing_preset.agent.max_tokens,
        agent_key=key,
        operation="update",
    )

    agents[key] = preset
    specs[key] = merged_spec
    if select_after_update:
        selected = key
    _save_generated_agent_library(work_path, agents, selected, specs)
    _save_persona_generation_trace(work_path, key, trace)
    return preset


async def aregenerate_agent_prompt_from_dependencies(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    work_path: Path,
    agent_key: str,
    model: str,
    dependency_files: list[str] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1400,
    select_after_update: bool = True,
) -> GeneratedSessionPreset:
    """Regenerate an existing agent by re-reading its dependency files from disk."""
    key = _normalize_agent_key(agent_key)
    agents, selected, specs = _load_library_full(work_path)

    if key not in agents:
        raise ValueError(f"找不到 agent：{agent_key}")
    if key not in specs:
        raise ValueError(
            f"agent '{agent_key}' 没有持久化的 PersonaSpec，"
            "请先通过 abuild_roleplay_prompt_from_answers_and_apply 生成初始版本。"
        )

    existing_preset = agents[key]
    dependency_values = dependency_files
    if dependency_values is None:
        dependency_values = specs[key].dependency_files
    if not dependency_values:
        raise ValueError("当前 agent 未配置 dependency_files，无法基于依赖文件重新生成")

    merged_spec = specs[key].merge(
        dependency_files=[_normalize_dependency_file_path(item) for item in dependency_values],
    )

    preset, trace = await _agenerate_from_persona_spec_with_trace(
        provider,
        merged_spec,
        model=model,
        dependency_root=work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=existing_preset.agent.model,
        base_temperature=existing_preset.agent.temperature,
        base_max_tokens=existing_preset.agent.max_tokens,
        agent_key=key,
        operation="regenerate_from_dependencies",
    )

    agents[key] = preset
    specs[key] = merged_spec
    if select_after_update:
        selected = key
    _save_generated_agent_library(work_path, agents, selected, specs)
    _save_persona_generation_trace(work_path, key, trace)
    return preset
