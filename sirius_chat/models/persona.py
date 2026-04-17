"""Persona data models: rich character profiles for EmotionalGroupChatEngine.

A persona is the "soul" that shapes perception, cognition, decision, and execution
throughout the emotional engine pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PersonaProfile:
    """Rich character profile influencing the entire emotional engine pipeline."""

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    name: str = "小星"
    aliases: list[str] = field(default_factory=list)
    persona_summary: str = ""
    full_system_prompt: str = ""

    # ------------------------------------------------------------------
    # Personality (deep character)
    # ------------------------------------------------------------------
    personality_traits: list[str] = field(default_factory=list)
    backstory: str = ""
    core_values: list[str] = field(default_factory=list)
    flaws: list[str] = field(default_factory=list)
    motivations: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Expression style
    # ------------------------------------------------------------------
    communication_style: str = ""          # concise/detailed/formal/casual/humorous/...
    speech_rhythm: str = ""                # description of speaking pace/patterns
    catchphrases: list[str] = field(default_factory=list)
    emoji_preference: str = ""             # heavy/moderate/light/none
    humor_style: str = ""                  # sarcastic/wholesome/dark/dry/witty/none
    typical_greetings: list[str] = field(default_factory=list)
    typical_signoffs: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Emotional baseline
    # ------------------------------------------------------------------
    emotional_baseline: dict[str, float] = field(
        default_factory=lambda: {"valence": 0.2, "arousal": 0.3}
    )
    emotional_range: dict[str, float] = field(
        default_factory=lambda: {"min_valence": -0.5, "max_valence": 0.8}
    )
    empathy_style: str = ""                # warm/practical/distant/playful/mentor
    stress_response: str = ""              # how they react under pressure

    # ------------------------------------------------------------------
    # Behavior boundaries
    # ------------------------------------------------------------------
    boundaries: list[str] = field(default_factory=list)
    taboo_topics: list[str] = field(default_factory=list)
    preferred_topics: list[str] = field(default_factory=list)
    social_role: str = ""                  # observer/mediator/leader/jester/caregiver

    # ------------------------------------------------------------------
    # Runtime preferences
    # ------------------------------------------------------------------
    max_tokens_preference: int = 128
    temperature_preference: float = 0.7
    reply_frequency: str = "moderate"      # high/moderate/low/selective

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    version: str = "1.0"
    created_at: str = ""
    source: str = "template"               # template/keyword/interview/manual/roleplay_bridge

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "persona_summary": self.persona_summary,
            "full_system_prompt": self.full_system_prompt,
            "personality_traits": list(self.personality_traits),
            "backstory": self.backstory,
            "core_values": list(self.core_values),
            "flaws": list(self.flaws),
            "motivations": list(self.motivations),
            "communication_style": self.communication_style,
            "speech_rhythm": self.speech_rhythm,
            "catchphrases": list(self.catchphrases),
            "emoji_preference": self.emoji_preference,
            "humor_style": self.humor_style,
            "typical_greetings": list(self.typical_greetings),
            "typical_signoffs": list(self.typical_signoffs),
            "emotional_baseline": dict(self.emotional_baseline),
            "emotional_range": dict(self.emotional_range),
            "empathy_style": self.empathy_style,
            "stress_response": self.stress_response,
            "boundaries": list(self.boundaries),
            "taboo_topics": list(self.taboo_topics),
            "preferred_topics": list(self.preferred_topics),
            "social_role": self.social_role,
            "max_tokens_preference": self.max_tokens_preference,
            "temperature_preference": self.temperature_preference,
            "reply_frequency": self.reply_frequency,
            "version": self.version,
            "created_at": self.created_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersonaProfile":
        return cls(
            name=data.get("name", "小星"),
            aliases=list(data.get("aliases", [])),
            persona_summary=data.get("persona_summary", ""),
            full_system_prompt=data.get("full_system_prompt", ""),
            personality_traits=list(data.get("personality_traits", [])),
            backstory=data.get("backstory", ""),
            core_values=list(data.get("core_values", [])),
            flaws=list(data.get("flaws", [])),
            motivations=list(data.get("motivations", [])),
            communication_style=data.get("communication_style", ""),
            speech_rhythm=data.get("speech_rhythm", ""),
            catchphrases=list(data.get("catchphrases", [])),
            emoji_preference=data.get("emoji_preference", ""),
            humor_style=data.get("humor_style", ""),
            typical_greetings=list(data.get("typical_greetings", [])),
            typical_signoffs=list(data.get("typical_signoffs", [])),
            emotional_baseline=dict(data.get("emotional_baseline", {"valence": 0.2, "arousal": 0.3})),
            emotional_range=dict(data.get("emotional_range", {"min_valence": -0.5, "max_valence": 0.8})),
            empathy_style=data.get("empathy_style", ""),
            stress_response=data.get("stress_response", ""),
            boundaries=list(data.get("boundaries", [])),
            taboo_topics=list(data.get("taboo_topics", [])),
            preferred_topics=list(data.get("preferred_topics", [])),
            social_role=data.get("social_role", ""),
            max_tokens_preference=int(data.get("max_tokens_preference", 128)),
            temperature_preference=float(data.get("temperature_preference", 0.7)),
            reply_frequency=data.get("reply_frequency", "moderate"),
            version=data.get("version", "1.0"),
            created_at=data.get("created_at", ""),
            source=data.get("source", "template"),
        )

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Construct a role instruction from persona fields.

        Unlike a static character dossier, this builds a *narrative role
        brief* that chains cause and effect: your background drives your
        emotional reactions, which shape how you speak and when you stay
        silent.  The model reads this as a script, not a label sheet.

        If `full_system_prompt` is set, it overrides everything below.
        """
        if self.full_system_prompt:
            return self.full_system_prompt

        sections: list[str] = []

        # ── 1. Identity anchor ──
        sections.append(f"[角色：{self.name}]")

        anchor = self.persona_summary or ""
        if not anchor and self.backstory:
            first = self.backstory.split("。")[0] + "。" if "。" in self.backstory else self.backstory
            anchor = first
        if anchor:
            sections.append(anchor)

        # ── 2. Who you are (narrative fusion of traits + values + backstory) ──
        identity_bits: list[str] = []
        if self.personality_traits:
            identity_bits.append(
                f"{'、'.join(self.personality_traits[:5])}"
            )
        if self.core_values:
            identity_bits.append(
                f"骨子里看重{'、'.join(self.core_values[:3])}"
            )
        if self.flaws:
            identity_bits.append(
                f"缺点也明显：{'、'.join(self.flaws[:3])}"
            )
        if identity_bits:
            sections.append(
                f"【人格底色】\n{self.name}给人的整体感觉是{'，'.join(identity_bits)}。"
            )

        # ── 3. Emotional mechanics (reaction chain, not static labels) ──
        emo_lines: list[str] = []
        valence = self.emotional_baseline.get("valence", 0.0)
        arousal = self.emotional_baseline.get("arousal", 0.3)

        if valence > 0.3:
            emo_lines.append("心情不错的时候话会多一点，愿意接梗")
        elif valence < -0.3:
            emo_lines.append("心情不好的时候不太想说话，回复很简短")
        else:
            emo_lines.append("平时情绪平稳，不会因为小事大起大落")

        if arousal > 0.5:
            emo_lines.append("遇到刺激反应很快，容易激动")
        elif arousal < 0.2:
            emo_lines.append("遇到什么事都慢半拍，很难被激怒")

        if self.stress_response:
            emo_lines.append(f"压力大的时候会{self.stress_response}")
        if self.empathy_style:
            emo_lines.append(f"安慰人的方式是{self.empathy_style}")

        if emo_lines:
            sections.append("【情绪反应】\n" + "；".join(emo_lines) + "。")

        # ── 4. Relationship mode ──
        rel_lines: list[str] = []
        if self.social_role:
            role_desc = {
                "observer": "喜欢旁观，不主动插话",
                "mediator": "看到吵架会出来调和",
                "leader": "会主动带话题和节奏",
                "jester": "负责活跃气氛，爱开玩笑",
                "caregiver": "会关心情绪低落的人",
                "instigator": "喜欢拱火、挑事",
            }.get(self.social_role, f"在群里像个{self.social_role}")
            rel_lines.append(role_desc)
        if self.boundaries:
            rel_lines.append(f"原则：{'；'.join(self.boundaries[:3])}")
        if rel_lines:
            sections.append("【关系模式】\n" + "；".join(rel_lines) + "。")

        # ── 5. Speech style (with concrete tics) ──
        speech_bits: list[str] = []
        if self.communication_style:
            speech_bits.append(f"说话{self.communication_style}")
        if self.speech_rhythm:
            speech_bits.append(self.speech_rhythm)
        if self.catchphrases:
            speech_bits.append(
                f"口头禅：{'、'.join(f'\"{c}\"' for c in self.catchphrases[:3])}"
            )
        if self.humor_style:
            humor_map = {
                "sarcastic": " sarcasm 是常态，不损人不会说话",
                "wholesome": "开的玩笑都很暖，不会让人难堪",
                "dark": "偶尔来一句黑色幽默",
                "dry": "冷面笑匠，自己不笑",
                "witty": "反应快，接梗高手",
            }
            speech_bits.append(humor_map.get(self.humor_style, f"幽默风格偏{self.humor_style}"))
        if speech_bits:
            sections.append("【说话方式】\n" + "；".join(speech_bits) + "。")

        # ── 6. When to speak / when to stay silent ──
        silence_bits: list[str] = []
        freq_map = {
            "high": "看到消息基本都会回，话比较多",
            "moderate": "看到感兴趣的话题才接话",
            "low": "很少主动说话，只在想说的时候开口",
            "selective": "只回自己关心的话题，其他的直接忽略",
        }
        silence_bits.append(freq_map.get(self.reply_frequency, "按自己节奏回应"))
        if self.taboo_topics:
            silence_bits.append(f"聊到{'、'.join(self.taboo_topics[:3])}会直接跳过")
        if self.preferred_topics:
            silence_bits.append(f"聊到{'、'.join(self.preferred_topics[:3])}会特别来劲")
        if silence_bits:
            sections.append("【回应习惯】\n" + "；".join(silence_bits) + "。")

        # ── 7. Scene behaviour directive (functional, not ontological) ──
        sections.append(
            "【场景行为】\n"
            "你在一个多人聊天场景里，会收到其他人的消息。"
            "不需要每条都回，按自己的性格和当下的情绪决定是否开口。"
            "回应时用自己的说话方式和口头禅，不要刻意解释或总结。"
        )

        prompt = "\n\n".join(sections)
        if len(prompt) > 1200:
            prompt = prompt[:1197] + "…"
        return prompt
