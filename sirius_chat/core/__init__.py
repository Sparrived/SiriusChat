"""
核心编排引擎模块

包含 AsyncRolePlayEngine，Sirius Chat 的主要编排和协调引擎。
"""

from sirius_chat.core.engine import AsyncRolePlayEngine
from sirius_chat.core.events import SessionEvent, SessionEventBus, SessionEventType
from sirius_chat.core.intent_v2 import IntentAnalysis, IntentAnalyzer
from sirius_chat.core.heat import HeatAnalysis, HeatAnalyzer
from sirius_chat.core.engagement import EngagementCoordinator, EngagementDecision

__all__ = [
    "AsyncRolePlayEngine",
    "EngagementCoordinator",
    "EngagementDecision",
    "HeatAnalysis",
    "HeatAnalyzer",
    "IntentAnalysis",
    "IntentAnalyzer",
    "SessionEvent",
    "SessionEventBus",
    "SessionEventType",
]
