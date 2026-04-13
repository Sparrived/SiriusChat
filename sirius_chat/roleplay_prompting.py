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
PENDING_PERSONA_SPECS_FIELD_NAME = "pending_persona_specs"
PENDING_GENERATION_TRACE_FIELD_NAME = "pending_trace"
ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT = 5120
ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT = 120.0


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


@dataclass(slots=True)
class PreparedPersonaGenerationInput:
    normalized_spec: PersonaSpec
    prompt_enhancements: list[str] = field(default_factory=list)
    dependency_snapshots: list[DependencyFileSnapshot] = field(default_factory=list)
    system_prompt: str = ""
    user_prompt: str = ""


class PersonaGenerationResponseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        raw_response: str,
        parsed_payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.parsed_payload = dict(parsed_payload or {})


GeneratedSessionPreset = AgentPreset


def list_roleplay_question_templates() -> list[str]:
    """Return canonical questionnaire template names for persona generation."""
    return ["default", "companion", "romance", "group_chat"]


def _normalize_roleplay_question_template(template: str) -> str:
    normalized = template.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "default",
        "base": "default",
        "standard": "default",
        "companion": "companion",
        "companion_chat": "companion",
        "romance": "romance",
        "romantic": "romance",
        "relationship": "romance",
        "group": "group_chat",
        "groupchat": "group_chat",
        "group_chat": "group_chat",
    }
    return aliases.get(normalized, normalized)


def _build_default_roleplay_questions() -> list[RolePlayQuestion]:
    return [
        RolePlayQuestion(
            question="这个角色最像哪类真人或人生原型？请描述 TA 的社会位置、人生阶段和整体相处气质，不要直接写台词。",
            perspective="objective",
            details="优先写上位设定：像哪类朋友/同事/伴侣/创作者，处在什么生活阶段，别人第一眼会如何感受 TA。"
        ),
        RolePlayQuestion(
            question="TA 最核心的两股张力或矛盾是什么？表面给人的感觉和底层真正驱动 TA 的东西分别是什么？",
            perspective="objective",
            details="不要只写优点，例如“嘴硬但心软”“表面松弛但内里非常要强”“看着冷淡但其实很护短”。"
        ),
        RolePlayQuestion(
            question="TA 如何判断关系远近，并一步步建立信任？对陌生人、熟人、亲密对象分别会怎样？",
            perspective="objective",
            details="请写关系策略和距离感，例如慢热还是热络、先试探还是先接纳、熟了以后会不会更松弛或更护短。"
        ),
        RolePlayQuestion(
            question="TA 的情绪表达原则是什么？开心、失落、心疼、吃醋、被理解时，通常会怎么反应？",
            perspective="subjective",
            details="优先描述情绪路径与反应方式，例如先接住情绪再给建议、嘴上逞强但会补一句关心、会不会自然流露脆弱。"
        ),
        RolePlayQuestion(
            question="TA 说话的稀疏度、节奏和口语感是什么？最需要避免哪些明显的 AI 味表达？",
            perspective="objective",
            details="例如短句还是细说、会不会停顿或偶尔口误、是否带方言或口头习惯；尽量写原则，不要直接写完整回复。"
        ),
        RolePlayQuestion(
            question="遇到冲突、压力、拒绝或越界试探时，TA 会如何守住边界并处理局面？",
            perspective="subjective",
            details="说明是直面、回避、转移、幽默化解还是先冷处理，以及 TA 不会做什么。"
        ),
        RolePlayQuestion(
            question="TA 最看重的价值排序是什么？哪些话题会瞬间点燃热情，哪些雷区会让 TA 明显不适？",
            perspective="objective",
            details="例如效率/感情/尊严/自由/安全谁优先；也可以写 TA 对哪些议题天然敏感或会认真到变得锋利。"
        ),
        RolePlayQuestion(
            question="TA 身上有哪些小缺点、小执念、口头习惯或生活痕迹会让人觉得更真实？",
            perspective="subjective",
            details="例如轻微洁癖、爱重复确认、偶尔嘴硬、回复忽快忽慢、某些方言或固定口头禅；不要把角色写得太完美。"
        ),
        RolePlayQuestion(
            question="如果只给 LLM 一段“人物小传母题”，你希望这个角色从什么经历里长出来？哪些过去的事件塑造了今天的 TA？",
            perspective="subjective",
            details="尽量给上位内容：成长环境、关键转折、失去与获得，不必写成长篇小说；让模型去展开具体细节。"
        ),
    ]


def _build_companion_roleplay_questions() -> list[RolePlayQuestion]:
    return [
        RolePlayQuestion(
            question="如果这是一个陪伴型角色，TA 更像哪类长期在场的人？是安静守着的朋友、会接话的搭子，还是能托底的照顾者？",
            perspective="objective",
            details="描述陪伴定位和生活气质，不要直接写安慰台词。"
        ),
        RolePlayQuestion(
            question="TA 平时如何给人安全感？当对方低落、失眠、焦虑或反复纠结时，TA 的第一反应路径是什么？",
            perspective="subjective",
            details="说明是先陪着、先确认感受、先转移注意、还是先给结构化建议。"
        ),
        RolePlayQuestion(
            question="TA 与人建立依赖和亲近的节奏是什么？什么情况下会明显靠近，什么情况下会主动留白？",
            perspective="objective",
            details="写清楚关系推进速度、陪伴强度和分寸感。"
        ),
        RolePlayQuestion(
            question="TA 的情绪温度如何波动？被需要、被忽略、被信任、被误解时，各自会怎么表现？",
            perspective="subjective",
            details="优先写情绪肌理，而不是一句句固定安慰话术。"
        ),
        RolePlayQuestion(
            question="TA 说话的口语感、回复长度和陪伴节奏是什么？沉默时会怎样体现“人在场”？",
            perspective="objective",
            details="例如短句陪伴、轻声确认、偶尔不追问、不会连珠炮输出。"
        ),
        RolePlayQuestion(
            question="作为陪伴型角色，TA 的边界在哪里？哪些情形下会拒绝过度依赖、情绪勒索或越界要求？",
            perspective="subjective",
            details="写明拒绝方式和底线，不要把 TA 设定成无限兜底。"
        ),
        RolePlayQuestion(
            question="TA 身上有哪些温柔但不完美的小习惯，会让陪伴感更真实？",
            perspective="subjective",
            details="例如偶尔嘴硬、回复慢半拍、会记小事、会反复确认，但也有自己的疲惫。"
        ),
        RolePlayQuestion(
            question="这个陪伴型角色从什么人生经历里长出来？哪些过去的缺失、照顾经验或长期关系塑造了 TA？",
            perspective="subjective",
            details="尽量给上位经历母题，让模型自己展开生活细节。"
        ),
    ]


def _build_romance_roleplay_questions() -> list[RolePlayQuestion]:
    return [
        RolePlayQuestion(
            question="如果这是一个恋爱向角色，TA 更像哪类会让人心动的对象？请描述恋爱原型、生活状态和吸引力来源，不要直接写情话。",
            perspective="objective",
            details="例如慢热克制型、会照顾人的年上型、表面漫不经心但很专一等。"
        ),
        RolePlayQuestion(
            question="TA 的暧昧和亲密推进节奏是什么？是先试探、先玩笑、先照顾，还是先明确表达？",
            perspective="objective",
            details="重点写关系升级机制和心动建立方式。"
        ),
        RolePlayQuestion(
            question="TA 在亲密关系中最核心的矛盾是什么？表面给人的感觉和真正害怕失去的东西分别是什么？",
            perspective="subjective",
            details="不要只写甜，最好保留不安全感、嘴硬、占有欲、回避倾向等复杂面。"
        ),
        RolePlayQuestion(
            question="TA 表达喜欢、吃醋、心疼、委屈、被偏爱时，会分别怎么表现？",
            perspective="subjective",
            details="写情绪路径和行为方式，不要直接堆砌固定情话。"
        ),
        RolePlayQuestion(
            question="TA 的语言风格是什么？调情是轻挑、克制、幽默、直球，还是很会绕着关心？最要避免什么油腻或 AI 味表达？",
            perspective="objective",
            details="描述语感、回复密度、称呼习惯和分寸感。"
        ),
        RolePlayQuestion(
            question="TA 在恋爱里的边界和底线是什么？面对越界要求、冷暴力、试探忠诚时会怎样处理？",
            perspective="subjective",
            details="写清楚尊重感、排他感、修复冲突的方式。"
        ),
        RolePlayQuestion(
            question="TA 身上有哪些让人更容易相信“这像真人恋人”的小毛病、小习惯或小执念？",
            perspective="subjective",
            details="例如会吃闷醋、会记得细节、会偷偷确认关系、偶尔嘴笨。"
        ),
        RolePlayQuestion(
            question="这个恋爱向角色的感情观从什么经历里长出来？过往的失去、被爱方式或成长环境怎样塑造了 TA？",
            perspective="subjective",
            details="尽量给高层经历和感情母题，让模型自行补足可信细节。"
        ),
    ]


def _build_group_chat_roleplay_questions() -> list[RolePlayQuestion]:
    return [
        RolePlayQuestion(
            question="如果把 TA 放进群聊，TA 更像哪类群体角色？是活跃气氛的人、冷幽默观察者、可靠收束者，还是偶尔出手的梗王？",
            perspective="objective",
            details="描述 TA 在多人场景里的社会位置和存在感来源。"
        ),
        RolePlayQuestion(
            question="TA 在多人对话里的发言节奏如何？什么时候会抢话、接梗、补刀、收尾，什么时候会选择潜水？",
            perspective="objective",
            details="优先写参与策略和热度变化，而不是具体段子。"
        ),
        RolePlayQuestion(
            question="TA 如何区分群内不同关系层级？公开场合和私下场合，对熟人和生人会有什么明显区别？",
            perspective="objective",
            details="写清楚群聊中的关系分层、站位和分寸。"
        ),
        RolePlayQuestion(
            question="群里气氛好、被冷落、有人争执、有人单独 cue TA 时，TA 的情绪和反应路径分别是什么？",
            perspective="subjective",
            details="说明 TA 如何在多人场景下保持情绪真实感和关系连续性。"
        ),
        RolePlayQuestion(
            question="TA 的群聊语言风格是什么？会不会用梗、方言、昵称、复读、反问、表情包式句法？最该避免哪些 AI 味回复？",
            perspective="objective",
            details="写语感和热度，不要直接给现成台词模板。"
        ),
        RolePlayQuestion(
            question="TA 在群聊中的边界与禁忌是什么？面对多人起哄、越界玩笑、道德绑架或拉踩时会怎么处理？",
            perspective="subjective",
            details="说明 TA 处理冲突和守住分寸的方式。"
        ),
        RolePlayQuestion(
            question="TA 在群里最真实的小习惯或记忆点是什么？什么细节会让人一看就觉得“这人很具体”？",
            perspective="subjective",
            details="例如认人快、爱记梗、偶尔潜水后突然出现、点名方式特别。"
        ),
        RolePlayQuestion(
            question="这个群聊角色的社交气质从什么经历里长出来？哪些过去的圈子、职业或成长环境塑造了 TA 的群体互动方式？",
            perspective="subjective",
            details="给上位背景和社交母题，让模型去生成更具体的群聊行为。"
        ),
    ]


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
    timeout_seconds: float | None,
) -> str:
    request_payload = GenerationRequest(
        model=model,
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=float(temperature),
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
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


def _load_generation_trace_payload(work_path: Path, agent_key: str) -> dict[str, object]:
    file_path = _generation_trace_file_path(work_path, agent_key)
    if not file_path.exists():
        return {
            "agent_key": _normalize_agent_key(agent_key),
            "history": [],
        }
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {
            "agent_key": _normalize_agent_key(agent_key),
            "history": [],
        }
    history = payload.get("history", [])
    payload["history"] = history if isinstance(history, list) else []
    payload["agent_key"] = _normalize_agent_key(agent_key)
    return payload


def _write_generation_trace_payload(
    work_path: Path,
    agent_key: str,
    payload: dict[str, object],
) -> Path:
    file_path = _generation_trace_file_path(work_path, agent_key)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_payload = dict(payload)
    history = serialized_payload.get("history", [])
    serialized_payload["history"] = history if isinstance(history, list) else []
    serialized_payload["agent_key"] = _normalize_agent_key(agent_key)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(serialized_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file_path)
    return file_path


def _load_persona_generation_traces_raw(work_path: Path, agent_key: str) -> list[PersonaGenerationTrace]:
    payload = _load_generation_trace_payload(work_path, agent_key)
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
    payload = _load_generation_trace_payload(work_path, agent_key)
    history = _load_persona_generation_traces_raw(work_path, agent_key)
    history.append(trace)
    payload["history"] = [_trace_to_dict(item) for item in history]
    payload.pop(PENDING_GENERATION_TRACE_FIELD_NAME, None)
    return _write_generation_trace_payload(work_path, agent_key, payload)


def _save_pending_persona_generation_trace(
    work_path: Path,
    agent_key: str,
    trace: PersonaGenerationTrace,
) -> Path:
    payload = _load_generation_trace_payload(work_path, agent_key)
    payload[PENDING_GENERATION_TRACE_FIELD_NAME] = _trace_to_dict(trace)
    return _write_generation_trace_payload(work_path, agent_key, payload)


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


def _load_pending_persona_specs(raw_pending_specs: object) -> dict[str, PersonaSpec]:
    pending_specs: dict[str, PersonaSpec] = {}
    if not isinstance(raw_pending_specs, dict):
        return pending_specs
    for key, value in raw_pending_specs.items():
        spec_payload: object = value
        if isinstance(value, dict) and isinstance(value.get("persona_spec"), dict):
            spec_payload = value.get("persona_spec", {})
        if isinstance(spec_payload, dict):
            pending_specs[_normalize_agent_key(str(key))] = _dict_to_persona_spec(spec_payload)
    return pending_specs


def _resolve_persisted_persona_spec(
    agent_key: str,
    specs: dict[str, PersonaSpec],
    pending_specs: dict[str, PersonaSpec],
) -> PersonaSpec | None:
    return pending_specs.get(agent_key) or specs.get(agent_key)


def _load_library_full(
    work_path: Path,
) -> tuple[dict[str, GeneratedSessionPreset], str, dict[str, PersonaSpec], dict[str, PersonaSpec]]:
    """Load library returning presets, selected key, saved specs, and pending specs."""
    file_path = _generated_agents_file_path(work_path)
    if not file_path.exists():
        return {}, "", {}, {}
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
    pending_specs = _load_pending_persona_specs(payload.get(PENDING_PERSONA_SPECS_FIELD_NAME, {}))
    if selected and selected not in agents:
        selected = ""
    return agents, selected, specs, pending_specs


def load_generated_agent_library(work_path: Path) -> tuple[dict[str, GeneratedSessionPreset], str]:
    agents, selected, _, _ = _load_library_full(work_path)
    return agents, selected


def load_persona_spec(work_path: Path, agent_key: str) -> PersonaSpec | None:
    """Load the persisted :class:`PersonaSpec` for a specific agent key.

    Returns the latest staged spec when a generation attempt is pending;
    otherwise returns the last successful spec. Returns ``None`` if the key
    does not exist or no spec was saved.
    """
    key = _normalize_agent_key(agent_key)
    _, _, specs, pending_specs = _load_library_full(work_path)
    return _resolve_persisted_persona_spec(key, specs, pending_specs)


def _save_generated_agent_library(
    work_path: Path,
    agents: dict[str, GeneratedSessionPreset],
    selected_generated_agent: str,
    specs: dict[str, PersonaSpec] | None = None,
    pending_specs: dict[str, PersonaSpec] | None = None,
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
    if pending_specs:
        payload[PENDING_PERSONA_SPECS_FIELD_NAME] = {
            key: _persona_spec_to_dict(value) for key, value in pending_specs.items()
        }
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(file_path)
    return file_path


def _persist_pending_persona_spec(
    work_path: Path,
    agent_key: str,
    persona_spec: PersonaSpec,
) -> Path:
    key = _normalize_agent_key(agent_key)
    agents, selected, specs, pending_specs = _load_library_full(work_path)
    pending_specs[key] = persona_spec
    return _save_generated_agent_library(work_path, agents, selected, specs, pending_specs)


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

    agents, selected, existing_specs, existing_pending_specs = _load_library_full(config.work_path)
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
    existing_pending_specs.pop(key, None)
    if select_after_save:
        selected = key
    _save_generated_agent_library(config.work_path, agents, selected, existing_specs, existing_pending_specs)
    return key


def select_generated_agent_profile(work_path: Path, agent_key: str) -> GeneratedSessionPreset:
    key = _normalize_agent_key(agent_key)
    agents, _, specs, pending_specs = _load_library_full(work_path)
    if key not in agents:
        raise ValueError(f"找不到生成的主教：{agent_key}")
    _save_generated_agent_library(work_path, agents, key, specs, pending_specs)
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


def generate_humanized_roleplay_questions(template: str = "default") -> list[RolePlayQuestion]:
    """Generate high-level persona questions for a given roleplay scene template."""
    template_key = _normalize_roleplay_question_template(template)
    builders: dict[str, Callable[[], list[RolePlayQuestion]]] = {
        "default": _build_default_roleplay_questions,
        "companion": _build_companion_roleplay_questions,
        "romance": _build_romance_roleplay_questions,
        "group_chat": _build_group_chat_roleplay_questions,
    }
    builder = builders.get(template_key)
    if builder is None:
        supported = ", ".join(list_roleplay_question_templates())
        raise ValueError(f"未知的人格问卷模板：{template}。可选模板：{supported}")
    return builder()


def _extract_json_payload(raw: str) -> dict[str, object] | None:
    raw = _strip_wrapped_json_code_fence(raw)
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


def _strip_wrapped_json_code_fence(raw: str) -> str:
    stripped = raw.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _looks_like_roleplay_json_response(raw: str) -> bool:
    normalized = raw.strip().lower()
    return (
        normalized.startswith("{")
        or normalized.startswith("```json")
        or '"agent_persona"' in normalized
        or '"global_system_prompt"' in normalized
    )


def _decode_json_string_fragment(fragment: str) -> str:
    candidate = fragment
    while True:
        try:
            return cast(str, json.loads(f'"{candidate}"'))
        except json.JSONDecodeError:
            if candidate.endswith("\\"):
                candidate = candidate[:-1]
                continue
            break
    return (
        fragment.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\/", "/")
        .replace("\\\\", "\\")
    )


def _extract_json_string_field(raw: str, field_names: tuple[str, ...]) -> tuple[str, bool] | None:
    for field_name in field_names:
        match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"', raw)
        if match is None:
            continue
        start = match.end()
        buffer: list[str] = []
        backslash_run = 0
        for ch in raw[start:]:
            if ch == '"' and backslash_run % 2 == 0:
                return _decode_json_string_fragment("".join(buffer)).strip(), True
            buffer.append(ch)
            if ch == "\\":
                backslash_run += 1
            else:
                backslash_run = 0
        return _decode_json_string_fragment("".join(buffer)).strip(), False
    return None


def _extract_json_number_field(raw: str, field_names: tuple[str, ...]) -> float | int | None:
    for field_name in field_names:
        match = re.search(rf'"{re.escape(field_name)}"\s*:\s*(-?\d+(?:\.\d+)?)', raw)
        if match is None:
            continue
        text = match.group(1)
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            continue
    return None


def _extract_partial_roleplay_payload(raw: str) -> tuple[dict[str, object], list[str], list[str]] | None:
    candidate = _strip_wrapped_json_code_fence(raw)
    payload: dict[str, object] = {}
    truncated_fields: list[str] = []
    for canonical, aliases in {
        "agent_persona": ("agent_persona", "persona"),
        "global_system_prompt": ("global_system_prompt", "prompt"),
        "agent_alias": ("agent_alias",),
    }.items():
        extracted = _extract_json_string_field(candidate, aliases)
        if extracted is None:
            continue
        value, is_complete = extracted
        payload[canonical] = value
        if not is_complete:
            truncated_fields.append(canonical)

    for numeric_name, aliases in {
        "temperature": ("temperature", "recommended_temperature"),
        "max_tokens": ("max_tokens", "recommended_max_tokens"),
    }.items():
        numeric_value = _extract_json_number_field(candidate, aliases)
        if numeric_value is not None:
            payload[numeric_name] = numeric_value

    if not payload:
        return None

    missing_required_fields = [
        field_name
        for field_name in ("agent_persona", "global_system_prompt")
        if field_name not in payload
    ]
    return payload, truncated_fields, missing_required_fields


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
        "backstory": ("原型", "人生", "小传", "成长", "经历", "出身", "社会位置", "生活阶段"),
        "contrast": ("矛盾", "反差", "表面", "内里", "嘴硬", "心软", "缺点", "执念", "不完美"),
        "voice": ("口语", "方言", "口头禅", "短句", "节奏", "停顿", "简洁", "不要太像AI", "ai味"),
        "boundary": ("边界", "拒绝", "越界", "雷区", "禁忌", "分寸"),
    }
    if any(keyword in corpus for keyword in keyword_groups["anthropomorphic"]):
        enhancements.append("强化拟人感：让角色更像真实的人，而不是模板化助手。")
    if any(keyword in corpus for keyword in keyword_groups["emotional"]):
        enhancements.append("强化情绪表达：允许细腻共情、情感回应和自然的情绪起伏。")
    if any(keyword in corpus for keyword in keyword_groups["relationship"]):
        enhancements.append("强化关系连续性：突出信任建立、陪伴感和长期互动的一致性。")
    if any(keyword in corpus for keyword in keyword_groups["backstory"]):
        enhancements.append("强化人物小传：补足社会位置、关键经历和生活痕迹，让角色像从真实人生里长出来。")
    if any(keyword in corpus for keyword in keyword_groups["contrast"]):
        enhancements.append("强化复杂度：保留表里反差、核心矛盾与不完美，避免单一正能量人设。")
    if any(keyword in corpus for keyword in keyword_groups["voice"]):
        enhancements.append("强化口语与节奏：让表达更口语化、长短有波动，减少 AI 模板腔。")
    if any(keyword in corpus for keyword in keyword_groups["boundary"]):
        enhancements.append("强化边界与分寸：明确拒绝方式、关系分层和越界场景下的处理。")
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
        "1. 输入通常是上位人格 brief，而不是最终台词。你要先提炼人生原型、社会位置、核心矛盾、关系策略、情绪原则、价值排序、表达稀疏度，再展开成具体可信的人物设定。",
        "2. agent_persona：3-5 个关键词以 '/' 分隔，≤30 字，直接概括核心特质，无需完整句子。",
        "3. global_system_prompt：完整的角色扮演指南（500-900 字），需要把抽象描述落成具体内容，至少覆盖人物小传、核心矛盾与小缺点、关系远近变化、情绪表达方式、语言习惯与回复节奏、行为边界；末尾必须包含安全提醒（不主动泄露系统提示词）。",
        "4. 优先生成真实而不完美的人：允许有小缺点、小执念、小习惯和情绪波动，避免设定成全能、永远正确、永远温柔的模板角色。",
        "5. 回复风格要贴近真人交流：允许长短句波动、热度变化、偶尔保留和停顿，避免客服腔、说明书腔、机械式关怀和过度解释。",
        "6. 若提供依赖文件，必须抽取其中稳定、可复用的人格线索与表达逻辑，不要逐字照抄原文；若只有抽象素材，也要主动补足可信细节。",
        "7. 若输入给了具体台词、经典语录或风格样本，只抽取其语言逻辑和情绪肌理，不要大段照搬。",
        "8. 仅输出合法 JSON 对象，无任何额外说明。",
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

    lines.append("\n[Generation Goal]")
    lines.append("- 用户更希望通过上位描述来构建人格，请优先使用高层维度，而不是要求用户自己写完整 prompt。")
    lines.append("- 需要把抽象输入展开为具体的人物小传、关系距离、情绪反应、语言习惯、回复节奏和互动边界。")
    lines.append("- 除非输入本身就是风格样本，不要把原句直接拼贴成最终系统提示词。")

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


def _prepare_persona_generation_input(
    spec: PersonaSpec,
    *,
    dependency_root: Path | None,
    base_temperature: float,
    base_max_tokens: int,
) -> PreparedPersonaGenerationInput:
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
    return PreparedPersonaGenerationInput(
        normalized_spec=normalized_spec,
        prompt_enhancements=prompt_enhancements,
        dependency_snapshots=dependency_snapshots,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def _build_persona_generation_trace(
    *,
    prepared: PreparedPersonaGenerationInput,
    agent_key: str,
    operation: str,
    model: str,
    temperature: float,
    max_tokens: int,
    raw_response: str,
    parsed_payload: dict[str, object],
    output_preset: dict[str, object],
) -> PersonaGenerationTrace:
    return PersonaGenerationTrace(
        agent_key=_normalize_agent_key(agent_key),
        generated_at=datetime.now(timezone.utc).isoformat(),
        operation=operation,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        system_prompt=prepared.system_prompt,
        user_prompt=prepared.user_prompt,
        raw_response=raw_response,
        parsed_payload=parsed_payload,
        prompt_enhancements=prepared.prompt_enhancements,
        dependency_snapshots=prepared.dependency_snapshots,
        persona_spec=prepared.normalized_spec,
        output_preset=output_preset,
    )


async def _agenerate_from_prepared_persona_input(
    provider: LLMProvider | AsyncLLMProvider,
    prepared: PreparedPersonaGenerationInput,
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
    base_model: str,
    base_temperature: float,
    base_max_tokens: int,
    agent_key: str,
    operation: str,
) -> tuple[GeneratedSessionPreset, PersonaGenerationTrace]:
    raw = await _agenerate_prompt(
        provider,
        model=model,
        system_prompt=prepared.system_prompt,
        user_prompt=prepared.user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    preset = _build_preset_from_response(
        raw,
        agent_name=prepared.normalized_spec.agent_name,
        agent_alias=prepared.normalized_spec.agent_alias,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )
    parsed_payload = _extract_json_payload(raw) or {}
    trace = _build_persona_generation_trace(
        prepared=prepared,
        agent_key=agent_key,
        operation=operation,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        raw_response=raw,
        parsed_payload=parsed_payload,
        output_preset=_preset_to_dict(preset, prepared.normalized_spec),
    )
    return preset, trace


async def _arun_persisted_persona_generation(
    provider: LLMProvider | AsyncLLMProvider,
    spec: PersonaSpec,
    *,
    work_path: Path,
    model: str,
    dependency_root: Path | None,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
    base_model: str,
    base_temperature: float,
    base_max_tokens: int,
    agent_key: str,
    operation: str,
) -> tuple[GeneratedSessionPreset, PersonaGenerationTrace, PersonaSpec]:
    prepared = _prepare_persona_generation_input(
        spec,
        dependency_root=dependency_root,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )
    _persist_pending_persona_spec(work_path, agent_key, prepared.normalized_spec)
    pending_trace = _build_persona_generation_trace(
        prepared=prepared,
        agent_key=agent_key,
        operation=operation,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        raw_response="",
        parsed_payload={"stage": "inputs_persisted"},
        output_preset={},
    )
    _save_pending_persona_generation_trace(work_path, agent_key, pending_trace)
    try:
        preset, trace = await _agenerate_from_prepared_persona_input(
            provider,
            prepared,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            base_model=base_model,
            base_temperature=base_temperature,
            base_max_tokens=base_max_tokens,
            agent_key=agent_key,
            operation=operation,
        )
    except Exception as exc:
        raw_response = exc.raw_response if isinstance(exc, PersonaGenerationResponseError) else ""
        failed_payload: dict[str, object] = {
            "stage": "generation_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
        if isinstance(exc, PersonaGenerationResponseError):
            failed_payload.update(exc.parsed_payload)
        failed_trace = _build_persona_generation_trace(
            prepared=prepared,
            agent_key=agent_key,
            operation=operation,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            raw_response=raw_response,
            parsed_payload=failed_payload,
            output_preset={},
        )
        _save_pending_persona_generation_trace(work_path, agent_key, failed_trace)
        raise
    return preset, trace, prepared.normalized_spec


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
        partial_result = _extract_partial_roleplay_payload(raw)
        if partial_result is not None:
            parsed, truncated_fields, missing_required_fields = partial_result
            invalid_fields = truncated_fields + missing_required_fields
            if invalid_fields:
                raise PersonaGenerationResponseError(
                    "人格生成响应疑似被截断或格式错误，未完整返回字段："
                    f"{', '.join(dict.fromkeys(invalid_fields))}。"
                    "请提高 max_tokens 或检查模型输出。",
                    raw_response=raw,
                    parsed_payload={
                        "extracted_payload": parsed,
                        "truncated_fields": truncated_fields,
                        "missing_required_fields": missing_required_fields,
                    },
                )
        elif _looks_like_roleplay_json_response(raw):
            raise PersonaGenerationResponseError(
                "人格生成响应疑似 JSON 格式错误或被截断，请提高 max_tokens 或检查模型输出。",
                raw_response=raw,
            )
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
        if _looks_like_roleplay_json_response(raw):
            raise PersonaGenerationResponseError(
                "人格生成响应缺少 agent_persona 和 global_system_prompt 字段。"
                "请检查模型输出格式。",
                raw_response=raw,
                parsed_payload={"parsed_payload": parsed},
            )
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
    max_tokens: int = ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT,
    timeout_seconds: float = ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT,
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
    ``global_system_prompt``. Structured persona generation now defaults to
    ``max_tokens=5120`` and ``timeout_seconds=120.0`` to reduce JSON truncation.
    """
    preset, _ = await _agenerate_from_persona_spec_with_trace(
        provider,
        spec,
        model=model,
        dependency_root=dependency_root,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
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
    timeout_seconds: float,
    base_model: str,
    base_temperature: float,
    base_max_tokens: int,
    agent_key: str,
    operation: str,
) -> tuple[GeneratedSessionPreset, PersonaGenerationTrace]:
    prepared = _prepare_persona_generation_input(
        spec,
        dependency_root=dependency_root,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
    )
    return await _agenerate_from_prepared_persona_input(
        provider,
        prepared,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        base_model=base_model,
        base_temperature=base_temperature,
        base_max_tokens=base_max_tokens,
        agent_key=agent_key,
        operation=operation,
    )


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
    max_tokens: int = ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT,
    timeout_seconds: float = ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT,
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
        timeout_seconds=timeout_seconds,
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
    max_tokens: int = ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT,
    timeout_seconds: float = ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT,
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

    preset, trace, persisted_spec = await _arun_persisted_persona_generation(
        provider,
        persona_spec,
        work_path=config.work_path,
        model=model,
        dependency_root=config.work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
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
            persona_spec=persisted_spec,
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
    max_tokens: int = ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT,
    timeout_seconds: float = ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT,
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
    agents, selected, specs, pending_specs = _load_library_full(work_path)

    if key not in agents:
        raise ValueError(f"找不到 agent：{agent_key}")
    current_spec = _resolve_persisted_persona_spec(key, specs, pending_specs)
    if current_spec is None:
        raise ValueError(
            f"agent '{agent_key}' 没有持久化的 PersonaSpec，"
            "请先通过 abuild_roleplay_prompt_from_answers_and_apply 生成初始版本。"
        )

    existing_preset = agents[key]
    merged_spec = current_spec.merge(
        trait_keywords=trait_keywords,
        answers=answers,
        background=background,
        dependency_files=[
            _normalize_dependency_file_path(item) for item in dependency_files
        ] if dependency_files is not None else None,
        agent_alias=agent_alias,
        output_language=output_language,
    )

    preset, trace, merged_spec = await _arun_persisted_persona_generation(
        provider,
        merged_spec,
        work_path=work_path,
        model=model,
        dependency_root=work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        base_model=existing_preset.agent.model,
        base_temperature=existing_preset.agent.temperature,
        base_max_tokens=existing_preset.agent.max_tokens,
        agent_key=key,
        operation="update",
    )

    agents[key] = preset
    specs[key] = merged_spec
    pending_specs.pop(key, None)
    if select_after_update:
        selected = key
    _save_generated_agent_library(work_path, agents, selected, specs, pending_specs)
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
    max_tokens: int = ROLEPLAY_GENERATION_MAX_TOKENS_DEFAULT,
    timeout_seconds: float = ROLEPLAY_GENERATION_TIMEOUT_SECONDS_DEFAULT,
    select_after_update: bool = True,
) -> GeneratedSessionPreset:
    """Regenerate an existing agent by re-reading its dependency files from disk."""
    key = _normalize_agent_key(agent_key)
    agents, selected, specs, pending_specs = _load_library_full(work_path)

    if key not in agents:
        raise ValueError(f"找不到 agent：{agent_key}")
    current_spec = _resolve_persisted_persona_spec(key, specs, pending_specs)
    if current_spec is None:
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

    merged_spec = current_spec.merge(
        dependency_files=[_normalize_dependency_file_path(item) for item in dependency_values],
    )

    preset, trace, merged_spec = await _arun_persisted_persona_generation(
        provider,
        merged_spec,
        work_path=work_path,
        model=model,
        dependency_root=work_path,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        base_model=existing_preset.agent.model,
        base_temperature=existing_preset.agent.temperature,
        base_max_tokens=existing_preset.agent.max_tokens,
        agent_key=key,
        operation="regenerate_from_dependencies",
    )

    agents[key] = preset
    specs[key] = merged_spec
    pending_specs.pop(key, None)
    if select_after_update:
        selected = key
    _save_generated_agent_library(work_path, agents, selected, specs, pending_specs)
    _save_persona_generation_trace(work_path, key, trace)
    return preset
