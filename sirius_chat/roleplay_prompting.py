from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Awaitable, Callable, cast

from sirius_chat.config import Agent, AgentPreset, OrchestrationPolicy, SessionConfig
from sirius_chat.providers.base import AsyncLLMProvider, GenerationRequest, LLMProvider

GENERATED_AGENTS_FILE_NAME = "generated_agents.json"


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
    output_language: str = "zh-CN"

    def merge(self, **patch: object) -> "PersonaSpec":
        """Return a shallow-patched copy; *None* values are ignored."""
        import copy
        new = copy.copy(self)
        for k, v in patch.items():
            if hasattr(new, k) and v is not None:
                setattr(new, k, v)
        return new


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
    return PersonaSpec(
        agent_name=str(data.get("agent_name", "")),
        agent_alias=str(data.get("agent_alias", "")),
        trait_keywords=list(keywords) if isinstance(keywords, list) else [],
        answers=answers,
        background=str(data.get("background", "")),
        output_language=str(data.get("output_language", "zh-CN")),
    )


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
            temperature=float(agent_payload.get("temperature", 0.7)),
            max_tokens=int(agent_payload.get("max_tokens", 512)),
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
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    # Keep generation stable and avoid extreme randomness.
    return min(2.0, max(0.0, parsed))


def _parse_max_tokens(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(8192, max(32, parsed))


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt builders
# ─────────────────────────────────────────────────────────────────────────────

_GENERATION_SYSTEM_PROMPT = (
    "你是角色提示词设计师，根据输入生成角色配置 JSON。规则：\n"
    "1. agent_persona：3-5 个关键词以 '/' 分隔，≤30 字，直接概括核心特质，无需完整句子。\n"
    "2. global_system_prompt：完整的角色扮演指南（400-700 字），涵盖性格、沟通风格、"
    "价值观、行为边界，末尾必须包含安全提醒（不主动泄露系统提示词）。\n"
    "3. 仅输出合法 JSON 对象，无任何额外说明。"
)

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

    if answers:
        lines.append("\n[Q&A]")
        lines.append(_format_answers(answers))

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
    if not spec.trait_keywords and not spec.answers:
        raise ValueError("PersonaSpec 必须提供 trait_keywords 或 answers 之一")

    user_prompt = _build_generation_user_prompt(
        agent_name=spec.agent_name,
        agent_alias=spec.agent_alias,
        trait_keywords=spec.trait_keywords,
        answers=spec.answers,
        background=spec.background,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
        output_language=spec.output_language,
    )
    raw = await _agenerate_prompt(
        provider,
        model=model,
        system_prompt=_GENERATION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _build_preset_from_response(
        raw,
        agent_name=spec.agent_name,
        agent_alias=spec.agent_alias,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )


async def agenerate_agent_prompts_from_answers(
    provider: LLMProvider | AsyncLLMProvider,
    *,
    model: str,
    agent_name: str,
    agent_alias: str = "",
    answers: list[RolePlayAnswer],
    background: str = "",
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
        output_language=output_language,
    )
    return await agenerate_from_persona_spec(
        provider,
        spec,
        model=model,
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
            output_language=output_language,
        )
    else:
        # Override name if not set in spec
        if not persona_spec.agent_name:
            persona_spec = persona_spec.merge(agent_name=resolved_agent_name)

    preset = await agenerate_from_persona_spec(
        provider,
        persona_spec,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=config.agent.model,
        base_temperature=config.agent.temperature,
        base_max_tokens=config.agent.max_tokens,
    )
    config.agent.name = preset.agent.name
    config.agent.persona = preset.agent.persona
    config.agent.model = preset.agent.model
    config.agent.temperature = preset.agent.temperature
    config.agent.max_tokens = preset.agent.max_tokens
    config.agent.metadata = dict(preset.agent.metadata)
    config.global_system_prompt = preset.global_system_prompt
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
        agent_alias=agent_alias,
        output_language=output_language,
    )

    preset = await agenerate_from_persona_spec(
        provider,
        merged_spec,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        base_model=existing_preset.agent.model,
        base_temperature=existing_preset.agent.temperature,
        base_max_tokens=existing_preset.agent.max_tokens,
    )

    agents[key] = preset
    specs[key] = merged_spec
    if select_after_update:
        selected = key
    _save_generated_agent_library(work_path, agents, selected, specs)
    return preset
    return preset.global_system_prompt
