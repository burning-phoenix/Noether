"""Tests for Planner mode system."""

import pytest
from src.modes.planner_modes import PlannerMode, PlanSubMode, PlannerModeState


class TestPlannerModeState:
    """Test PlannerModeState dataclass."""

    def test_defaults(self):
        state = PlannerModeState()
        assert state.mode == PlannerMode.GO
        assert state.plan_sub_mode == PlanSubMode.MAINTAINABLE

    def test_is_go_mode_default(self):
        state = PlannerModeState()
        assert state.is_go_mode() is True
        assert state.is_plan_mode() is False

    def test_is_plan_mode(self):
        state = PlannerModeState(mode=PlannerMode.PLAN)
        assert state.is_go_mode() is False
        assert state.is_plan_mode() is True

    def test_is_maintainable(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.MAINTAINABLE)
        assert state.is_maintainable() is True
        assert state.is_discovery() is False

    def test_is_discovery(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.DISCOVERY)
        assert state.is_maintainable() is False
        assert state.is_discovery() is True

    def test_is_maintainable_requires_plan_mode(self):
        # In GO mode, even if sub_mode is MAINTAINABLE, is_maintainable is False
        state = PlannerModeState(mode=PlannerMode.GO, plan_sub_mode=PlanSubMode.MAINTAINABLE)
        assert state.is_maintainable() is False

    def test_is_discovery_requires_plan_mode(self):
        state = PlannerModeState(mode=PlannerMode.GO, plan_sub_mode=PlanSubMode.DISCOVERY)
        assert state.is_discovery() is False

    def test_display_string_go(self):
        state = PlannerModeState(mode=PlannerMode.GO)
        assert state.get_display_string() == "Go Mode"

    def test_display_string_plan_maintainable(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.MAINTAINABLE)
        assert state.get_display_string() == "Plan Mode (Maintainable)"

    def test_display_string_plan_discovery(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.DISCOVERY)
        assert state.get_display_string() == "Plan Mode (Discovery)"

    def test_mode_switch_preserves_sub_mode(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.DISCOVERY)
        state.mode = PlannerMode.GO
        # Sub-mode is preserved even though it's not active
        assert state.plan_sub_mode == PlanSubMode.DISCOVERY
        # But it shouldn't affect GO mode behavior
        assert state.is_go_mode() is True
        assert state.is_discovery() is False

    def test_sub_mode_switch_within_plan(self):
        state = PlannerModeState(mode=PlannerMode.PLAN, plan_sub_mode=PlanSubMode.MAINTAINABLE)
        assert state.is_maintainable() is True
        state.plan_sub_mode = PlanSubMode.DISCOVERY
        assert state.is_maintainable() is False
        assert state.is_discovery() is True


class TestPlannerModeEnum:
    """Test enum values."""

    def test_go_value(self):
        assert PlannerMode.GO.value == "go"

    def test_plan_value(self):
        assert PlannerMode.PLAN.value == "plan"

    def test_maintainable_value(self):
        assert PlanSubMode.MAINTAINABLE.value == "maintainable"

    def test_discovery_value(self):
        assert PlanSubMode.DISCOVERY.value == "discovery"
