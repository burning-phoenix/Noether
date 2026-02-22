"""
Planner operational modes for controlling agent behavior.

Modes:
- GO: Action-first, minimal questions, direct execution
- PLAN: Socratic method with two sub-modes:
  - MAINTAINABLE: Focus on architecture, testability, documentation
  - DISCOVERY: Focus on prototyping, MVPs, experimentation
"""

from dataclasses import dataclass, field
from enum import Enum


class PlannerMode(Enum):
    """Top-level Planner operational mode."""
    GO = "go"
    PLAN = "plan"


class PlanSubMode(Enum):
    """Sub-modes within PLAN mode."""
    MAINTAINABLE = "maintainable"
    DISCOVERY = "discovery"


@dataclass
class PlannerModeState:
    """Tracks the current Planner mode and sub-mode."""
    mode: PlannerMode = PlannerMode.GO
    plan_sub_mode: PlanSubMode = PlanSubMode.MAINTAINABLE

    def is_go_mode(self) -> bool:
        return self.mode == PlannerMode.GO

    def is_plan_mode(self) -> bool:
        return self.mode == PlannerMode.PLAN

    def is_maintainable(self) -> bool:
        return self.mode == PlannerMode.PLAN and self.plan_sub_mode == PlanSubMode.MAINTAINABLE

    def is_discovery(self) -> bool:
        return self.mode == PlannerMode.PLAN and self.plan_sub_mode == PlanSubMode.DISCOVERY

    def get_display_string(self) -> str:
        if self.mode == PlannerMode.GO:
            return "Go Mode"
        if self.plan_sub_mode == PlanSubMode.MAINTAINABLE:
            return "Plan Mode (Maintainable)"
        return "Plan Mode (Discovery)"
