"""Agent base protocol and CaddyAgent orchestrator.

Designed with protocol-based architecture for iOS portability:
- Each tool follows the AgentTool protocol
- CLI implementations use Python + APIs
- iOS implementations will use Swift + native frameworks
- The CaddyAgent orchestrator works with either
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from kindcaddy.round_state import RoundState


@dataclass
class Alert:
    """A proactive message the agent wants to deliver to the golfer."""

    source: str
    priority: str  # "low", "medium", "high"
    message: str
    data: dict = field(default_factory=dict)


@runtime_checkable
class AgentTool(Protocol):
    """Base protocol for all agent tools.

    On iOS, each tool gets a native Swift implementation.
    In CLI, each tool uses Python + APIs.
    The interface stays the same.
    """

    name: str

    def check(self, round_state: "RoundState") -> Optional[Alert]:
        """Check if this tool has a proactive alert to surface.
        Called by the agent on each trigger cycle."""
        ...

    def execute(self, params: dict) -> dict:
        """Execute a specific tool action on demand."""
        ...

    def reset(self) -> None:
        """Reset tool state for a new round."""
        ...


class CaddyAgent:
    """Orchestrates all agent tools and manages the trigger loop.

    In CLI: triggers fire after each user interaction.
    On iOS: triggers fire on GPS movement, timers, app lifecycle events.
    """

    def __init__(self, tools: list[AgentTool] | None = None):
        self.tools: list[AgentTool] = tools or []
        self._pending_alerts: list[Alert] = []

    def register_tool(self, tool: AgentTool) -> None:
        self.tools.append(tool)

    def on_trigger(self, round_state: "RoundState", trigger_type: str = "interaction") -> list[Alert]:
        """Run all tools, collect any proactive alerts.

        Args:
            round_state: Current round state shared by all tools.
            trigger_type: What caused the trigger.
                CLI: "interaction", "hole_change", "score_logged", "shot_logged"
                iOS (future): "gps_movement", "timer", "app_foreground"
        """
        alerts = []
        for tool in self.tools:
            try:
                alert = tool.check(round_state)
                if alert:
                    alerts.append(alert)
            except Exception:
                logger.exception("Agent tool '%s' failed during check", getattr(tool, "name", "unknown"))

        self._pending_alerts.extend(alerts)
        return alerts

    def get_pending_alerts(self) -> list[Alert]:
        """Get and clear pending alerts for injection into LLM context."""
        alerts = self._pending_alerts.copy()
        self._pending_alerts.clear()
        return alerts

    def has_pending_alerts(self) -> bool:
        return len(self._pending_alerts) > 0

    def reset_for_new_round(self) -> None:
        """Reset all tools for a new round."""
        self._pending_alerts.clear()
        for tool in self.tools:
            try:
                tool.reset()
            except Exception:
                logger.exception("Agent tool '%s' failed during reset", getattr(tool, "name", "unknown"))

    def get_tool(self, name: str) -> Optional[AgentTool]:
        """Get a tool by name."""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None
