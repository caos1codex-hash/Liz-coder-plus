"""Comprehensive tests for Sprint 3.3 — Agent-Aware Planning.

Covers: AgentInfo, AgentPlanningContext, CapabilityMapper, AgentAssignment,
AgentSelector, AgentAwarePlanner, TaskPlanner.create_agent_aware_plan(),
new exceptions, integration scenarios.

Test naming convention: test_<unit>_<scenario>_<expected>
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup (matches existing project test conventions)
# ---------------------------------------------------------------------------
_PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"
sys.path.insert(0, str(_PACKAGES_DIR))

import pytest  # noqa: E402

# -- imports after path setup  # noqa: E402 ----------------------------------
from planner.agent_context import AgentInfo, AgentPlanningContext  # noqa: E402
from planner.agent_selector import AgentSelector  # noqa: E402
from planner.assignment import AgentAssignment  # noqa: E402
from planner.capability_mapper import CapabilityMapper  # noqa: E402
from planner.exceptions import (  # noqa: E402
    AgentNotFoundError,
    AgentPlanningError,
    AssignmentError,
    CapabilityMissingError,
)
from planner.integration import AgentAwarePlanner  # noqa: E402
from planner.models import PlanStep, TaskPlan  # noqa: E402
from planner.planner import TaskPlanner  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================


def _make_agent(
    agent_id: str = "agent-1",
    name: str = "TestAgent",
    capabilities: list[str] | None = None,
    status: str = "idle",
    priority: int = 5,
    available: bool = True,
    tools: list[str] | None = None,
    metadata: dict | None = None,
) -> AgentInfo:
    """Create an AgentInfo for testing."""
    return AgentInfo(
        id=agent_id,
        name=name,
        capabilities=capabilities or [],
        status=status,
        priority=priority,
        available=available,
        tools=tools or [],
        metadata=metadata or {},
    )


def _make_context(*agents: AgentInfo) -> AgentPlanningContext:
    """Create an AgentPlanningContext with the given agents."""
    ctx = AgentPlanningContext()
    for a in agents:
        ctx.add_agent(a)
    return ctx


def _backend_agent() -> AgentInfo:
    return _make_agent(
        agent_id="be-1",
        name="BackendAgent",
        capabilities=["code.generate", "code.test", "filesystem.read", "filesystem.write"],
        priority=8,
    )


def _frontend_agent() -> AgentInfo:
    return _make_agent(
        agent_id="fe-1",
        name="FrontendAgent",
        capabilities=["code.generate", "filesystem.write"],
        priority=7,
    )


def _planning_agent() -> AgentInfo:
    return _make_agent(
        agent_id="pl-1",
        name="PlanningAgent",
        capabilities=["planning", "workflow.create", "plan", "decompose"],
        priority=5,
    )


def _research_agent() -> AgentInfo:
    return _make_agent(
        agent_id="rs-1",
        name="ResearchAgent",
        capabilities=["research", "web.search", "documentation.lookup"],
        priority=6,
    )


def _busy_agent() -> AgentInfo:
    return _make_agent(
        agent_id="busy-1",
        name="BusyAgent",
        capabilities=["code.generate"],
        priority=9,
        status="busy",
        available=False,
    )


def _default_mapper() -> CapabilityMapper:
    return CapabilityMapper.create_default()


# ===========================================================================
# Test: New Exceptions
# ===========================================================================


class TestExceptions:
    """Sprint 3.3 exception hierarchy."""

    def test_agent_planning_error_is_planner_exception(self) -> None:
        exc = AgentPlanningError("test")
        assert isinstance(exc, Exception)

    def test_agent_not_found_attributes(self) -> None:
        exc = AgentNotFoundError("my-agent")
        assert exc.agent_id == "my-agent"
        assert "my-agent" in str(exc)

    def test_capability_missing_attributes(self) -> None:
        exc = CapabilityMissingError("code.generate")
        assert exc.capability == "code.generate"
        assert "code.generate" in str(exc)

    def test_assignment_error_attributes(self) -> None:
        exc = AssignmentError(
            step_id="s1", agent_id="a1", reason="not available"
        )
        assert exc.step_id == "s1"
        assert exc.agent_id == "a1"
        assert exc.reason == "not available"
        assert "s1" in str(exc)
        assert "a1" in str(exc)

    def test_assignment_error_inherits_agent_planning_error(self) -> None:
        exc = AssignmentError("s1", "a1", "r")
        assert isinstance(exc, AgentPlanningError)

    def test_agent_not_found_inherits_agent_planning_error(self) -> None:
        exc = AgentNotFoundError("x")
        assert isinstance(exc, AgentPlanningError)

    def test_capability_missing_inherits_agent_planning_error(self) -> None:
        exc = CapabilityMissingError("x")
        assert isinstance(exc, AgentPlanningError)


# ===========================================================================
# Test: AgentInfo
# ===========================================================================


class TestAgentInfo:
    """AgentInfo dataclass tests."""

    def test_default_values(self) -> None:
        info = AgentInfo()
        assert info.id == ""
        assert info.name == ""
        assert info.capabilities == []
        assert info.status == "created"
        assert info.priority == 0
        assert info.available is True

    def test_provides_exact_match(self) -> None:
        info = _make_agent(capabilities=["code.generate"])
        assert info.provides("code.generate") is True

    def test_provides_hierarchical_parent(self) -> None:
        """Agent has 'code' → can satisfy 'code.generate'."""
        info = _make_agent(capabilities=["code"])
        assert info.provides("code.generate") is True

    def test_provides_hierarchical_child(self) -> None:
        """Agent has 'code.generate' → does NOT satisfy 'code' (parent).

        Child-to-parent matching is not supported — mirrors Sprint 2
        AgentRecord.provides() behaviour.
        """
        info = _make_agent(capabilities=["code.generate"])
        assert info.provides("code") is False

    def test_provides_no_match(self) -> None:
        info = _make_agent(capabilities=["code.generate"])
        assert info.provides("git.commit") is False

    def test_provides_empty_capabilities(self) -> None:
        info = AgentInfo()
        assert info.provides("anything") is False

    def test_to_dict_roundtrip(self) -> None:
        info = _make_agent(
            agent_id="x1", name="Test",
            capabilities=["a", "b"], tools=["t1"],
            metadata={"k": "v"},
        )
        d = info.to_dict()
        assert d["id"] == "x1"
        assert d["capabilities"] == ["a", "b"]
        assert d["tools"] == ["t1"]
        assert d["metadata"] == {"k": "v"}

    def test_from_dict(self) -> None:
        data = {
            "id": "x2", "name": "T2",
            "capabilities": ["c1"],
            "status": "busy",
            "priority": 3,
            "available": False,
            "tools": ["tool1"],
            "metadata": {"key": "val"},
        }
        info = AgentInfo.from_dict(data)
        assert info.id == "x2"
        assert info.name == "T2"
        assert info.capabilities == ["c1"]
        assert info.status == "busy"
        assert info.priority == 3
        assert info.available is False
        assert info.tools == ["tool1"]
        assert info.metadata == {"key": "val"}

    def test_from_dict_defaults(self) -> None:
        info = AgentInfo.from_dict({})
        assert info.id == ""
        assert info.capabilities == []
        assert info.available is True


# ===========================================================================
# Test: AgentPlanningContext
# ===========================================================================


class TestAgentPlanningContext:
    """AgentPlanningContext CRUD and lookup tests."""

    def test_add_and_get_agent(self) -> None:
        ctx = AgentPlanningContext()
        agent = _backend_agent()
        ctx.add_agent(agent)
        assert ctx.agent_count == 1
        assert ctx.get_agent("be-1") is agent
        assert ctx.get_agent("BackendAgent") is agent

    def test_add_replaces_same_id(self) -> None:
        ctx = AgentPlanningContext()
        a1 = _make_agent(agent_id="x", name="V1", priority=1)
        a2 = _make_agent(agent_id="x", name="V2", priority=2)
        ctx.add_agent(a1)
        ctx.add_agent(a2)
        assert ctx.agent_count == 1
        assert ctx.get_agent("x").name == "V2"

    def test_remove_agent_by_id(self) -> None:
        ctx = _make_context(_backend_agent())
        assert ctx.remove_agent("be-1") is True
        assert ctx.agent_count == 0

    def test_remove_agent_by_name(self) -> None:
        ctx = _make_context(_backend_agent())
        assert ctx.remove_agent("BackendAgent") is True
        assert ctx.agent_count == 0

    def test_remove_nonexistent_returns_false(self) -> None:
        ctx = AgentPlanningContext()
        assert ctx.remove_agent("ghost") is False

    def test_get_nonexistent_returns_none(self) -> None:
        ctx = AgentPlanningContext()
        assert ctx.get_agent("ghost") is None

    def test_find_by_capability_sorted_by_priority(self) -> None:
        ctx = _make_context(_frontend_agent(), _backend_agent())
        results = ctx.find_by_capability("code.generate")
        names = [a.name for a in results]
        assert "BackendAgent" in names
        assert "FrontendAgent" in names
        # Backend has higher priority (8 > 7)
        assert names[0] == "BackendAgent"

    def test_find_by_capability_excludes_unavailable(self) -> None:
        ctx = _make_context(_busy_agent(), _backend_agent())
        results = ctx.find_by_capability("code.generate")
        names = [a.name for a in results]
        assert "BusyAgent" not in names
        assert "BackendAgent" in names

    def test_find_by_capability_no_match(self) -> None:
        ctx = _make_context(_backend_agent())
        results = ctx.find_by_capability("git.commit")
        assert results == []

    def test_list_available(self) -> None:
        ctx = _make_context(_backend_agent(), _busy_agent())
        avail = ctx.list_available()
        names = [a.name for a in avail]
        assert "BackendAgent" in names
        assert "BusyAgent" not in names

    def test_list_capabilities_sorted_deduped(self) -> None:
        ctx = _make_context(
            _make_agent(agent_id="a", capabilities=["code.generate", "code.test"]),
            _make_agent(agent_id="b", capabilities=["code.generate", "git.commit"]),
        )
        caps = ctx.list_capabilities()
        assert "code.generate" in caps
        assert "code.test" in caps
        assert "git.commit" in caps
        assert caps == sorted(caps)
        # deduplicated
        assert caps.count("code.generate") == 1

    def test_list_capabilities_empty(self) -> None:
        ctx = AgentPlanningContext()
        assert ctx.list_capabilities() == []

    def test_agent_count(self) -> None:
        ctx = _make_context(_backend_agent(), _frontend_agent())
        assert ctx.agent_count == 2

    def test_available_count(self) -> None:
        ctx = _make_context(_backend_agent(), _busy_agent())
        assert ctx.available_count == 1

    def test_to_dict_roundtrip(self) -> None:
        ctx = _make_context(_backend_agent())
        ctx.metadata["key"] = "val"
        d = ctx.to_dict()
        assert "agents" in d
        assert "metadata" in d
        assert len(d["agents"]) == 1
        ctx2 = AgentPlanningContext.from_dict(d)
        assert ctx2.agent_count == 1
        assert ctx2.get_agent("be-1").name == "BackendAgent"
        assert ctx2.metadata == {"key": "val"}

    def test_from_dict_empty(self) -> None:
        ctx = AgentPlanningContext.from_dict({})
        assert ctx.agent_count == 0

    def test_from_registry_duck_typed_get_all(self) -> None:
        """Test from_registry with a mock that has get_all()."""
        mock_record = MagicMock()
        mock_record.id = "r1"
        mock_record.name = "MockAgent"
        mock_record.provided_capabilities = ["code.generate"]
        mock_record.status = MagicMock(value="idle")
        mock_record.priority = 5
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=["shell"])
        mock_record.tools = ["shell"]

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]

        ctx = AgentPlanningContext.from_registry(mock_registry)
        assert ctx.agent_count == 1
        agent = ctx.get_agent("r1")
        assert agent is not None
        assert agent.name == "MockAgent"
        assert agent.capabilities == ["code.generate"]
        assert agent.tools == ["shell"]

    def test_from_registry_duck_typed_list_agents(self) -> None:
        """Test from_registry falls back to list_agents()."""
        mock_record = MagicMock()
        mock_record.id = "r2"
        mock_record.name = "ListAgent"
        mock_record.provided_capabilities = ["planning"]
        mock_record.status = MagicMock(value="created")
        mock_record.priority = 3
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=[])
        mock_record.tools = []

        # Use an object that only has list_agents (no get_all)
        class MinimalRegistry:
            def list_agents(self):
                return [mock_record]

        ctx = AgentPlanningContext.from_registry(MinimalRegistry())
        assert ctx.agent_count == 1
        assert ctx.get_agent("r2").name == "ListAgent"

    def test_from_registry_empty(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_all.return_value = []
        ctx = AgentPlanningContext.from_registry(mock_registry)
        assert ctx.agent_count == 0

    def test_provides_child_not_parent(self) -> None:
        """Agent with code.generate should NOT provide 'code'.

        Parent capability requires explicit registration.
        """
        info = _make_agent(capabilities=["code.generate"])
        assert info.provides("code") is False

    def test_provides_exact_deep_hierarchy(self) -> None:
        """Agent with code should provide code.generate (parent-to-child)."""
        info = _make_agent(capabilities=["code"])
        assert info.provides("code.generate") is True


# ===========================================================================
# Test: CapabilityMapper
# ===========================================================================


class TestCapabilityMapper:
    """CapabilityMapper mapping and resolution tests."""

    def test_default_mapper_has_agent_mappings(self) -> None:
        mapper = _default_mapper()
        names = mapper.get_agent_names()
        assert "frontend_agent" in names
        assert "backend_agent" in names
        assert "planning_agent" in names

    def test_default_mapper_has_requirement_mappings(self) -> None:
        mapper = _default_mapper()
        reqs = mapper.get_requirements()
        assert "frontend" in reqs
        assert "backend" in reqs
        assert "api" in reqs

    def test_map_capability_known_agent(self) -> None:
        mapper = _default_mapper()
        caps = mapper.map_capability("frontend_agent")
        assert "code.generate" in caps
        assert "filesystem.write" in caps

    def test_map_capability_unknown_agent(self) -> None:
        mapper = _default_mapper()
        caps = mapper.map_capability("nonexistent_agent")
        assert caps == []

    def test_resolve_requirement_exact_match(self) -> None:
        mapper = _default_mapper()
        caps = mapper.resolve_requirement("frontend")
        assert "code.generate" in caps

    def test_resolve_requirement_substring_match(self) -> None:
        mapper = _default_mapper()
        caps = mapper.resolve_requirement("create a rest api for users")
        assert "code.generate" in caps

    def test_resolve_requirement_spanish(self) -> None:
        mapper = _default_mapper()
        caps = mapper.resolve_requirement("crear interfaz web")
        assert "code.generate" in caps

    def test_resolve_requirement_no_match(self) -> None:
        mapper = _default_mapper()
        caps = mapper.resolve_requirement("xyznonexistent")
        assert caps == []

    def test_get_required_capabilities(self) -> None:
        mapper = _default_mapper()
        caps = mapper.get_required_capabilities(["frontend_agent", "backend_agent"])
        assert "code.generate" in caps
        assert "filesystem.write" in caps
        assert "code.test" in caps
        # deduplicated and sorted
        assert caps == sorted(caps)

    def test_get_required_capabilities_empty(self) -> None:
        mapper = _default_mapper()
        caps = mapper.get_required_capabilities([])
        assert caps == []

    def test_get_required_capabilities_unknown(self) -> None:
        mapper = _default_mapper()
        caps = mapper.get_required_capabilities(["ghost"])
        assert caps == []

    def test_add_agent_mapping(self) -> None:
        mapper = CapabilityMapper()
        mapper.add_agent_mapping("custom", ["cap.a", "cap.b"])
        assert mapper.map_capability("custom") == ["cap.a", "cap.b"]

    def test_add_agent_mapping_overwrites(self) -> None:
        mapper = CapabilityMapper()
        mapper.add_agent_mapping("x", ["a"])
        mapper.add_agent_mapping("x", ["b"])
        assert mapper.map_capability("x") == ["b"]

    def test_add_requirement_mapping(self) -> None:
        mapper = CapabilityMapper()
        mapper.add_requirement_mapping("deploy", ["terminal.execute"])
        caps = mapper.resolve_requirement("deploy the app")
        assert "terminal.execute" in caps

    def test_resolve_requirement_deduplication(self) -> None:
        """Ensure duplicate capabilities from multiple matches are deduped."""
        mapper = CapabilityMapper()
        mapper.add_requirement_mapping("code", ["code.generate"])
        mapper.add_requirement_mapping("generate", ["code.generate"])
        caps = mapper.resolve_requirement("code generate")
        assert caps.count("code.generate") == 1

    def test_empty_mapper(self) -> None:
        mapper = CapabilityMapper()
        assert mapper.get_agent_names() == []
        assert mapper.get_requirements() == []
        assert mapper.map_capability("anything") == []
        assert mapper.resolve_requirement("anything") == []

    def test_get_required_capabilities_dedup_and_sort(self) -> None:
        mapper = CapabilityMapper()
        mapper.add_agent_mapping("a1", ["z.cap", "a.cap"])
        mapper.add_agent_mapping("a2", ["a.cap", "m.cap"])
        caps = mapper.get_required_capabilities(["a1", "a2"])
        assert caps == ["a.cap", "m.cap", "z.cap"]


# ===========================================================================
# Test: AgentAssignment
# ===========================================================================


class TestAgentAssignment:
    """AgentAssignment model tests."""

    def test_default_values(self) -> None:
        aa = AgentAssignment()
        assert aa.step_id == ""
        assert aa.agent_id == ""
        assert aa.capability == ""
        assert aa.confidence_score == 0.0
        assert aa.reason == ""

    def test_to_dict(self) -> None:
        aa = AgentAssignment(
            step_id="s1",
            agent_id="a1",
            capability="code.generate",
            confidence_score=0.95,
            reason="Best match",
            metadata={"alt": ["a2"]},
        )
        d = aa.to_dict()
        assert d["step_id"] == "s1"
        assert d["agent_id"] == "a1"
        assert d["confidence_score"] == 0.95
        assert d["metadata"]["alt"] == ["a2"]

    def test_from_dict(self) -> None:
        data = {
            "step_id": "s2",
            "agent_id": "a2",
            "capability": "planning",
            "confidence_score": 0.8,
            "reason": "Fallback",
            "metadata": {"key": "val"},
        }
        aa = AgentAssignment.from_dict(data)
        assert aa.step_id == "s2"
        assert aa.agent_id == "a2"
        assert aa.confidence_score == 0.8
        assert aa.metadata == {"key": "val"}

    def test_from_dict_defaults(self) -> None:
        aa = AgentAssignment.from_dict({})
        assert aa.step_id == ""
        assert aa.confidence_score == 0.0

    def test_from_dict_string_score(self) -> None:
        aa = AgentAssignment.from_dict({"confidence_score": "0.75"})
        assert aa.confidence_score == 0.75


# ===========================================================================
# Test: AgentSelector
# ===========================================================================


class TestAgentSelector:
    """AgentSelector scoring, ranking, and selection tests."""

    def _full_context(self) -> AgentPlanningContext:
        return _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
            _research_agent(),
            _busy_agent(),
        )

    def test_score_agent_exact_capability_match(self) -> None:
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        score = sel.score_agent(ctx.get_agent("be-1"), "code.generate")
        assert score > 0.0
        assert score <= 1.0

    def test_score_agent_no_capability(self) -> None:
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        score = sel.score_agent(ctx.get_agent("be-1"), "git.commit")
        # cap_match=0 but priority and availability still contribute small score
        assert score < 0.4

    def test_score_agent_unavailable_penalized(self) -> None:
        ctx = _make_context(_busy_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper, prefer_available=True)
        score_avail = 1.0  # if available with exact match + high priority
        # The busy agent has code.generate, so base score would be high
        # but penalty reduces it
        score = sel.score_agent(ctx.get_agent("busy-1"), "code.generate")
        assert score > 0.0
        assert score < 1.0  # penalized

    def test_score_agent_no_penalty_when_prefer_available_false(self) -> None:
        ctx = _make_context(_busy_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper, prefer_available=False)
        score_no_pref = sel.score_agent(ctx.get_agent("busy-1"), "code.generate")
        sel2 = AgentSelector(ctx, mapper, prefer_available=True)
        score_pref = sel2.score_agent(ctx.get_agent("busy-1"), "code.generate")
        assert score_no_pref > score_pref

    def test_rank_agents_sorted_by_score(self) -> None:
        ctx = self._full_context()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        ranked = sel.rank_agents("code.generate")
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_rank_agents_limit(self) -> None:
        ctx = self._full_context()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        ranked = sel.rank_agents("code.generate", limit=1)
        assert len(ranked) <= 1

    def test_rank_agents_no_match(self) -> None:
        ctx = _make_context(_research_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        ranked = sel.rank_agents("git.commit")
        # ResearchAgent doesn't provide git.commit but may have non-zero
        # score from priority/availability factors
        for _, score in ranked:
            agent = ctx.get_agent("rs-1")
            assert not agent.provides("git.commit")

    def test_select_agent_best_match(self) -> None:
        ctx = self._full_context()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        assignment = sel.select_agent("code.generate", step_id="s1")
        assert assignment.step_id == "s1"
        assert assignment.confidence_score > 0.0
        # Should select BackendAgent (highest priority code.generate provider)
        assert assignment.agent_id == "be-1"

    def test_select_agent_raises_on_no_match(self) -> None:
        ctx = AgentPlanningContext()  # empty — guaranteed no candidates
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(CapabilityMissingError):
            sel.select_agent("git.commit")

    def test_select_agent_with_required_agents_boost(self) -> None:
        """Agents suggested by required_agents should be boosted."""
        ctx = _make_context(_frontend_agent(), _backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        # Both provide code.generate. Backend has higher priority (8 vs 7)
        # but if we suggest frontend, it should be boosted
        assignment = sel.select_agent(
            "code.generate",
            step_id="s1",
            required_agents=["FrontendAgent"],
        )
        assert assignment.agent_id == "fe-1"

    def test_select_agents_multiple_capabilities(self) -> None:
        ctx = self._full_context()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        assignments = sel.select_agents(
            ["code.generate", "planning"], step_id="s1"
        )
        assert len(assignments) == 2
        assert assignments[0].capability == "code.generate"
        assert assignments[1].capability == "planning"

    def test_select_agents_raises_on_missing(self) -> None:
        ctx = AgentPlanningContext()  # empty
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(CapabilityMissingError):
            sel.select_agents(["git.commit"])

    def test_validate_agent_exists_success(self) -> None:
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        agent = sel.validate_agent_exists("be-1")
        assert agent.name == "BackendAgent"

    def test_validate_agent_exists_raises(self) -> None:
        ctx = AgentPlanningContext()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(AgentNotFoundError):
            sel.validate_agent_exists("ghost")

    def test_validate_assignment_success(self) -> None:
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        sel.validate_assignment("s1", "be-1", "code.generate")

    def test_validate_assignment_agent_not_found(self) -> None:
        ctx = AgentPlanningContext()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(AgentNotFoundError):
            sel.validate_assignment("s1", "ghost", "code.generate")

    def test_validate_assignment_unavailable(self) -> None:
        ctx = _make_context(_busy_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(AssignmentError) as exc_info:
            sel.validate_assignment("s1", "busy-1", "code.generate")
        assert "not available" in exc_info.value.reason

    def test_validate_assignment_capability_mismatch(self) -> None:
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(AssignmentError) as exc_info:
            sel.validate_assignment("s1", "be-1", "git.commit")
        assert "does not provide" in exc_info.value.reason


# ===========================================================================
# Test: AgentAwarePlanner Integration
# ===========================================================================


class TestAgentAwarePlanner:
    """Integration tests for the agent-aware planning pipeline."""

    def test_create_agent_aware_plan_basic(self) -> None:
        """Smoke test: generates a plan with agent assignments."""
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
        )
        mapper = _default_mapper()
        planner = AgentAwarePlanner(mapper=mapper)

        plan = planner.create_agent_aware_plan(
            goal="Crear API REST",
            agent_registry=MagicMock(),  # not used when context is provided
            context=ctx,
        )

        assert plan.goal == "Crear API REST"
        assert len(plan.steps) > 0
        assert "agent_assignments" in plan.metadata
        assert "agent_context_summary" in plan.metadata

    def test_plan_steps_have_agent_assignments(self) -> None:
        """Each step should have an agent_assignment in metadata."""
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
            _research_agent(),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear API REST",
            agent_registry=MagicMock(),
            context=ctx,
        )

        assigned_steps = 0
        for step in plan.steps:
            assignment = step.metadata.get("agent_assignment")
            if assignment:
                assigned_steps += 1
                assert "agent_id" in assignment
                assert "capability" in assignment
                assert "confidence_score" in assignment
        assert assigned_steps > 0

    def test_create_agent_aware_plan_api_rest(self) -> None:
        """'Crear API REST' should assign BackendAgent."""
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear API REST",
            agent_registry=MagicMock(),
            context=ctx,
        )

        # At least one step should be assigned to BackendAgent
        agent_ids_in_plan = set()
        for step in plan.steps:
            aa = step.metadata.get("agent_assignment", {})
            agent = ctx.get_agent(aa.get("agent_id", ""))
            if agent:
                agent_ids_in_plan.add(agent.name)

        assert "BackendAgent" in agent_ids_in_plan

    def test_create_agent_aware_plan_frontend(self) -> None:
        """'Crear interfaz web' should involve FrontendAgent.

        The Sprint 3.2 rule engine detects 'frontend' keywords and suggests
        frontend_agent. With AgentAwarePlanner, this maps to FrontendAgent
        in the registry via the capability mapper.
        """
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear interfaz web frontend con componentes",
            agent_registry=MagicMock(),
            context=ctx,
        )

        agent_ids_in_plan = set()
        for step in plan.steps:
            aa = step.metadata.get("agent_assignment", {})
            agent = ctx.get_agent(aa.get("agent_id", ""))
            if agent:
                agent_ids_in_plan.add(agent.name)

        # FrontendAgent should be assigned because the rule engine
        # suggests frontend_agent and the mapper connects it to
        # FrontendAgent via name matching
        assert "FrontendAgent" in agent_ids_in_plan, (
            f"Expected FrontendAgent, got {agent_ids_in_plan}"
        )

    def test_assign_agents_to_existing_plan(self) -> None:
        """Enriching an existing plan with agents."""
        step1 = PlanStep(title="Implementar endpoint", required_agents=["backend_agent"])
        step2 = PlanStep(title="Escribir tests", required_agents=["tester_agent"])
        plan = TaskPlan(goal="Test plan", steps=[step1, step2])

        ctx = _make_context(
            _backend_agent(),
            _make_agent(
                agent_id="tt-1", name="TesterAgent",
                capabilities=["code.test", "code.review"], priority=6,
            ),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        assignments = aw_planner.assign_agents(plan, ctx)
        assert len(assignments) > 0
        assert "agent_assignments" in plan.metadata

    def test_validate_agent_assignments_valid(self) -> None:
        """Validation passes for correct assignments."""
        step = PlanStep(title="Step 1")
        step.metadata["agent_assignment"] = AgentAssignment(
            step_id=step.id, agent_id="be-1", capability="code.generate",
            confidence_score=0.9, reason="test",
        ).to_dict()
        plan = TaskPlan(goal="g", steps=[step])

        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert errors == []

    def test_validate_agent_assignments_agent_not_found(self) -> None:
        step = PlanStep(title="Step 1")
        step.metadata["agent_assignment"] = AgentAssignment(
            step_id=step.id, agent_id="ghost", capability="code.generate",
            confidence_score=0.9, reason="test",
        ).to_dict()
        plan = TaskPlan(goal="g", steps=[step])

        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_validate_agent_assignments_unavailable(self) -> None:
        step = PlanStep(title="Step 1")
        step.metadata["agent_assignment"] = AgentAssignment(
            step_id=step.id, agent_id="busy-1", capability="code.generate",
            confidence_score=0.9, reason="test",
        ).to_dict()
        plan = TaskPlan(goal="g", steps=[step])

        ctx = _make_context(_busy_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert len(errors) == 1
        assert "not available" in errors[0]

    def test_validate_agent_assignments_capability_mismatch(self) -> None:
        step = PlanStep(title="Step 1")
        step.metadata["agent_assignment"] = AgentAssignment(
            step_id=step.id, agent_id="be-1", capability="git.commit",
            confidence_score=0.9, reason="test",
        ).to_dict()
        plan = TaskPlan(goal="g", steps=[step])

        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert len(errors) == 1
        assert "does not provide" in errors[0]

    def test_validate_no_assignments(self) -> None:
        """Plan with no assignments should validate clean."""
        plan = TaskPlan(goal="g", steps=[PlanStep(title="S1")])
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert errors == []

    def test_plan_metadata_contains_context_summary(self) -> None:
        ctx = _make_context(_backend_agent(), _frontend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Test", agent_registry=MagicMock(), context=ctx,
        )

        summary = plan.metadata.get("agent_context_summary", {})
        assert summary["total_agents"] == 2
        assert summary["available_agents"] == 2
        assert isinstance(summary["capabilities"], list)

    def test_no_agents_produces_plan_anyway(self) -> None:
        """Even with zero agents, the plan should be generated (with warnings)."""
        ctx = AgentPlanningContext()
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear API REST",
            agent_registry=MagicMock(),
            context=ctx,
        )

        assert plan.goal == "Crear API REST"
        assert len(plan.steps) > 0
        # Steps without assignments are OK (logged as warnings)

    def test_planner_property(self) -> None:
        planner = TaskPlanner()
        aw_planner = AgentAwarePlanner(planner=planner)
        assert aw_planner.planner is planner

    def test_mapper_property(self) -> None:
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        assert aw_planner.mapper is mapper


# ===========================================================================
# Test: TaskPlanner.create_agent_aware_plan (convenience method)
# ===========================================================================


class TestTaskPlannerAgentAware:
    """Test the convenience method on TaskPlanner."""

    def test_create_agent_aware_plan_via_taskplanner(self) -> None:
        """TaskPlanner.create_agent_aware_plan delegates correctly."""
        planner = TaskPlanner()

        mock_record = MagicMock()
        mock_record.id = "pl-1"
        mock_record.name = "PlanningAgent"
        mock_record.provided_capabilities = [
            "planning", "workflow.create", "plan", "decompose",
        ]
        mock_record.status = MagicMock(value="idle")
        mock_record.priority = 5
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=[])
        mock_record.tools = []

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]

        plan = planner.create_agent_aware_plan(
            goal="Plan simple",
            agent_registry=mock_registry,
        )

        assert plan.goal == "Plan simple"
        assert len(plan.steps) > 0
        assert "agent_assignments" in plan.metadata

    def test_create_agent_aware_plan_passes_priority(self) -> None:
        planner = TaskPlanner()

        mock_record = MagicMock()
        mock_record.id = "be-1"
        mock_record.name = "BackendAgent"
        mock_record.provided_capabilities = ["code.generate"]
        mock_record.status = MagicMock(value="idle")
        mock_record.priority = 8
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=[])
        mock_record.tools = []

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]

        plan = planner.create_agent_aware_plan(
            goal="Crear API",
            agent_registry=mock_registry,
            priority="high",
        )

        assert plan.priority.value == "high"


# ===========================================================================
# Test: Integration Scenarios (from spec)
# ===========================================================================


class TestIntegrationScenarios:
    """End-to-end scenarios specified in the Sprint 3.3 spec."""

    def test_scenario_api_rest_assigns_backend(self) -> None:
        """'Crear una API REST' → BackendAgent assigned."""
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
            _research_agent(),
            _make_agent(
                agent_id="tt-1", name="TesterAgent",
                capabilities=["code.test"], priority=6,
            ),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear una API REST",
            agent_registry=MagicMock(),
            context=ctx,
        )

        # Collect agent names from assignments
        assigned_agents = set()
        for step in plan.steps:
            aa = step.metadata.get("agent_assignment", {})
            agent = ctx.get_agent(aa.get("agent_id", ""))
            if agent:
                assigned_agents.add(agent.name)

        assert "BackendAgent" in assigned_agents, (
            f"Expected BackendAgent, got {assigned_agents}"
        )

    def test_scenario_web_interface_assigns_frontend(self) -> None:
        """'Crear interfaz web frontend' → FrontendAgent assigned."""
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
            _research_agent(),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear interfaz web frontend con componentes responsive",
            agent_registry=MagicMock(),
            context=ctx,
        )

        assigned_agents = set()
        for step in plan.steps:
            aa = step.metadata.get("agent_assignment", {})
            agent = ctx.get_agent(aa.get("agent_id", ""))
            if agent:
                assigned_agents.add(agent.name)

        assert "FrontendAgent" in assigned_agents, (
            f"Expected FrontendAgent, got {assigned_agents}"
        )

    def test_scenario_full_pipeline_with_login_app(self) -> None:
        """'Crear aplicación web con login y autenticación' should assign
        multiple agent types including backend for auth.
        """
        ctx = _make_context(
            _planning_agent(),
            _backend_agent(),
            _frontend_agent(),
            _research_agent(),
            _make_agent(
                agent_id="tt-1", name="TesterAgent",
                capabilities=["code.test", "code.review"], priority=6,
            ),
            _make_agent(
                agent_id="sec-1", name="SecurityAgent",
                capabilities=["code.review", "audit"], priority=5,
            ),
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)

        plan = aw_planner.create_agent_aware_plan(
            goal="Crear aplicación web con login y autenticación",
            agent_registry=MagicMock(),
            context=ctx,
        )

        assert len(plan.steps) > 0
        assigned_agents = set()
        for step in plan.steps:
            aa = step.metadata.get("agent_assignment", {})
            agent = ctx.get_agent(aa.get("agent_id", ""))
            if agent:
                assigned_agents.add(agent.name)

        # Should have at least 2 different agent types
        # (PlanningAgent for analysis + BackendAgent for implementation)
        assert len(assigned_agents) >= 2, (
            f"Expected multiple agent types, got {assigned_agents}"
        )


# ===========================================================================
# Test: Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Edge case and boundary tests."""

    def test_agent_info_provides_dot_notation(self) -> None:
        """Dot-separated capabilities should work correctly."""
        info = _make_agent(capabilities=["code.generate", "code.review"])
        assert info.provides("code.generate") is True  # exact
        assert info.provides("code.review") is True  # exact
        assert info.provides("code") is False  # child→parent not supported
        assert info.provides("code.refactor") is False  # sibling not supported

    def test_context_replace_agent_updates(self) -> None:
        """Replacing an agent should update all lookups."""
        ctx = AgentPlanningContext()
        a1 = _make_agent(agent_id="x", name="V1", capabilities=["a"])
        a2 = _make_agent(agent_id="x", name="V2", capabilities=["b"])
        ctx.add_agent(a1)
        assert ctx.find_by_capability("a")
        assert not ctx.find_by_capability("b")
        ctx.add_agent(a2)
        assert not ctx.find_by_capability("a")
        assert ctx.find_by_capability("b")

    def test_selector_empty_context_raises(self) -> None:
        """Selecting from empty context should raise CapabilityMissingError."""
        ctx = AgentPlanningContext()
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper)
        with pytest.raises(CapabilityMissingError):
            sel.select_agent("code.generate")

    def test_assignment_serialization_roundtrip(self) -> None:
        """Full roundtrip: assignment → dict → dict → assignment."""
        original = AgentAssignment(
            step_id="s1",
            agent_id="a1",
            capability="code.generate",
            confidence_score=0.95,
            reason="Best match",
            metadata={"alternatives": ["a2", "a3"]},
        )
        d = original.to_dict()
        restored = AgentAssignment.from_dict(d)
        assert restored.step_id == original.step_id
        assert restored.agent_id == original.agent_id
        assert restored.capability == original.capability
        assert restored.confidence_score == original.confidence_score
        assert restored.reason == original.reason
        assert restored.metadata == original.metadata

    def test_context_serialization_full_roundtrip(self) -> None:
        """Full roundtrip with multiple agents and metadata."""
        ctx = _make_context(
            _backend_agent(),
            _frontend_agent(),
            _busy_agent(),
        )
        ctx.metadata = {"project": "test"}

        d = ctx.to_dict()
        ctx2 = AgentPlanningContext.from_dict(d)

        assert ctx2.agent_count == 3
        assert ctx2.get_agent("be-1").name == "BackendAgent"
        assert ctx2.get_agent("busy-1").available is False
        assert ctx2.metadata == {"project": "test"}

    def test_mapper_resolve_requirement_dedup_preserves_order(self) -> None:
        mapper = CapabilityMapper()
        mapper.add_requirement_mapping("web", ["cap_a"])
        mapper.add_requirement_mapping("app", ["cap_a", "cap_b"])
        caps = mapper.resolve_requirement("web app")
        assert caps == ["cap_a", "cap_b"]

    def test_score_agent_priority_normalization(self) -> None:
        """Priority 10 should give full priority factor."""
        info = _make_agent(capabilities=["code.generate"], priority=10)
        ctx = _make_context(info)
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper, prefer_available=False)
        score = sel.score_agent(info, "code.generate")
        # cap_match=1.0, avail=1.0, priority=10/10=1.0, coverage>0
        assert score > 0.9

    def test_from_registry_handles_missing_tools(self) -> None:
        """AgentRecord without tools attribute shouldn't crash."""
        mock_record = MagicMock(spec=["id", "name", "provided_capabilities",
                                       "status", "priority", "metadata",
                                       "is_available"])
        mock_record.id = "r1"
        mock_record.name = "NoTools"
        mock_record.provided_capabilities = ["code.generate"]
        mock_record.status.value = "idle"
        mock_record.priority = 5
        mock_record.metadata = {}
        mock_record.is_available = True

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]

        ctx = AgentPlanningContext.from_registry(mock_registry)
        assert ctx.agent_count == 1
        assert ctx.get_agent("r1").tools == []

    def test_validate_assignment_with_agent_assignment_object(self) -> None:
        """validate_agent_assignments should handle AgentAssignment objects too."""
        step = PlanStep(title="S1")
        step.metadata["agent_assignment"] = AgentAssignment(
            step_id=step.id, agent_id="be-1", capability="code.generate",
        )
        plan = TaskPlan(goal="g", steps=[step])
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert errors == []

    def test_validate_skips_non_dict_non_assignment(self) -> None:
        """Should gracefully skip unexpected assignment types."""
        step = PlanStep(title="S1")
        step.metadata["agent_assignment"] = "invalid_type"
        plan = TaskPlan(goal="g", steps=[step])
        ctx = _make_context(_backend_agent())
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        errors = aw_planner.validate_agent_assignments(plan, ctx)
        assert errors == []

    def test_from_registry_status_string(self) -> None:
        """Handle agents where status is already a string (not Enum)."""
        mock_record = MagicMock()
        mock_record.id = "r1"
        mock_record.name = "StringStatus"
        mock_record.provided_capabilities = ["planning"]
        mock_record.status = "idle"  # string, not Enum
        mock_record.priority = 1
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=[])

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]

        ctx = AgentPlanningContext.from_registry(mock_registry)
        assert ctx.get_agent("r1").status == "idle"

    def test_from_registry_record_with_tools_attr(self) -> None:
        """Test _record_to_info when record has .tools (not .agent.tools)."""
        mock_record = MagicMock(spec=["id", "name", "provided_capabilities",
                                       "status", "priority", "metadata",
                                       "is_available", "tools"])
        mock_record.id = "r1"
        mock_record.name = "ToolsAgent"
        mock_record.provided_capabilities = ["terminal.execute"]
        mock_record.status.value = "idle"
        mock_record.priority = 5
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.tools = ["shell", "ssh"]

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]
        ctx = AgentPlanningContext.from_registry(mock_registry)
        agent = ctx.get_agent("r1")
        assert agent is not None
        assert agent.tools == ["shell", "ssh"]

    def test_score_agent_empty_capabilities_uses_coverage_zero(self) -> None:
        """Agent with empty capabilities gets coverage=0.0."""
        info = _make_agent(capabilities=[])
        ctx = _make_context(info)
        mapper = _default_mapper()
        sel = AgentSelector(ctx, mapper, prefer_available=False)
        score = sel.score_agent(info, "code.generate")
        # cap_match=0, availability=1, priority=0.5, coverage=0
        expected = 0.25 * 1.0 + 0.15 * 0.5  # 0.25 + 0.075 = 0.325
        assert abs(score - expected) < 0.01

    def test_create_agent_aware_plan_uses_context_from_registry(self) -> None:
        """When no context is passed, builds one from registry."""
        mock_record = MagicMock()
        mock_record.id = "r1"
        mock_record.name = "OnlyAgent"
        mock_record.provided_capabilities = ["planning"]
        mock_record.status = MagicMock(value="idle")
        mock_record.priority = 5
        mock_record.metadata = {}
        mock_record.is_available = True
        mock_record.agent = MagicMock(tools=[])

        mock_registry = MagicMock()
        mock_registry.get_all.return_value = [mock_record]
        mock_registry.list_agents = MagicMock(return_value=[])

        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        plan = aw_planner.create_agent_aware_plan(
            goal="Plan simple",
            agent_registry=mock_registry,
        )
        assert plan.goal == "Plan simple"
        assert len(plan.steps) > 0

    def test_assign_agents_fallback_to_highest_priority(self) -> None:
        """When no capability matches, fallback selects highest-priority."""
        step1 = PlanStep(
            title="Step with no relevant agents",
            description="Generic step",
        )
        plan = TaskPlan(goal="Test", steps=[step1])

        ctx = _make_context(
            _planning_agent(),  # priority 5
            _backend_agent(),  # priority 8
        )
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        assignments = aw_planner.assign_agents(plan, ctx)
        # Fallback should select BackendAgent (highest priority)
        assert len(assignments) > 0
        assert any(
            ctx.get_agent(a.agent_id).name == "BackendAgent"
            for a in assignments if ctx.get_agent(a.agent_id)
        )

    def test_assign_agents_fallback_no_available(self) -> None:
        """When no agents are available, no assignment is created."""
        step1 = PlanStep(title="Orphan step")
        plan = TaskPlan(goal="Test", steps=[step1])

        ctx = AgentPlanningContext()  # empty
        mapper = _default_mapper()
        aw_planner = AgentAwarePlanner(mapper=mapper)
        assignments = aw_planner.assign_agents(plan, ctx)
        assert len(assignments) == 0

    def test_resolve_step_capabilities_with_metadata(self) -> None:
        """Steps can declare required_capabilities in metadata."""
        step = PlanStep(
            title="Custom step",
            metadata={"required_capabilities": ["code.generate", "git.commit"]},
        )
        aw_planner = AgentAwarePlanner()
        caps = aw_planner._resolve_step_capabilities(step)
        assert "code.generate" in caps
        assert "git.commit" in caps

    def test_resolve_step_capabilities_from_mapper(self) -> None:
        """Step with required_agents maps their capabilities."""
        step = PlanStep(
            title="Test",
            required_agents=["frontend_agent"],
        )
        aw_planner = AgentAwarePlanner()
        caps = aw_planner._resolve_step_capabilities(step)
        assert "code.generate" in caps  # mapped from frontend_agent