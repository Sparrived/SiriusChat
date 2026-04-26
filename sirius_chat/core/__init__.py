"""
核心编排引擎模块 (v1.0.0)

EmotionalGroupChatEngine 是默认引擎。
"""

from sirius_chat.core.emotional_engine import EmotionalGroupChatEngine
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.core.rhythm import RhythmAnalysis, RhythmAnalyzer
from sirius_chat.core.response_strategy import ResponseStrategyEngine
from sirius_chat.core.delayed_response_queue import DelayedResponseQueue
from sirius_chat.core.proactive_trigger import ProactiveTrigger
from sirius_chat.core.model_router import ModelRouter, TaskConfig
from sirius_chat.core.response_assembler import ResponseAssembler, StyleAdapter, StyleParams
from sirius_chat.core.threshold_engine import ThresholdEngine

__all__ = [
    # v0.28+ Emotional Group Chat
    "EmotionalGroupChatEngine",
    "IntentAnalyzerV3",
    "EmotionAnalyzer",
    "RhythmAnalysis",
    "RhythmAnalyzer",
    "ResponseStrategyEngine",
    "DelayedResponseQueue",
    "ProactiveTrigger",
    "ThresholdEngine",
    "ModelRouter",
    "TaskConfig",
    "ResponseAssembler",
    "StyleAdapter",
    "StyleParams",
    # Shared
    "SessionEvent",
    "SessionEventBus",
    "SessionEventType",
]
