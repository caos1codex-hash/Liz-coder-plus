"""Comprehensive tests for the planner package.

Sprint 3.1 — Intelligent Task Planning Engine.

Covers: enums, models, exceptions, interfaces, validator, serializer,
metrics, planner (create/clone/validate/estimate/serialize/deserialize).

Test naming convention: test_<unit>_<scenario>_<expected>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup (matches existing project test conventions)
# ---------------------------------------------------------------------------
# Point to packages/ so we can import ``planner.enums``, ``planner.models``, etc.
_PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"
sys.path.insert(0, str(_PACKAGES_DIR))

import pytest  # noqa: E402

# -- imports after path setup  # noqa: E402 ----------------------------------
from planner.enums import (  # noqa: E402
    PlanStatus,
    Priority,
    StepStatus,
    can_transition_plan,
    can_transition_step,
)
from planner.exceptions import (  # noqa: E402
    CircularDependencyError,
    DependencyError,
    DuplicateStepError,
    InvalidStatusError,
    PlannerException,
    PlanValidationError,
    SerializationError,
)
from planner.interfaces import IMetrics, IPlanner, ISerializer, IValidator  # noqa: E402
from planner.metrics import PlannerMetrics  # noqa: E402
from planner.models import PlanMetrics, PlanStep, TaskPlan  # noqa: E402
from planner.planner import TaskPlanner  # noqa: E402
from planner.serializer import PlanSerializer  # noqa: E402
from planner.validator import PlanValidator  # noqa: E402

# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def empty_plan() -> TaskPlan:
    return TaskPlan(goal="test goal")


@pytest.fixture
def simple_steps() -> list[PlanStep]:
    return [
        PlanStep(id="s1", title="Step 1", description="First step", estimated_duration=10.0),
        PlanStep(
            id="s2",
            title="Step 2",
            description="Second step",
            estimated_duration=20.0,
            dependencies=["s1"],
        ),
        PlanStep(
            id="s3",
            title="Step 3",
            description="Third step",
            estimated_duration=5.0,
            dependencies=["s1"],
        ),
    ]


@pytest.fixture
def plan_with_steps(simple_steps: list[PlanStep]) -> TaskPlan:
    return TaskPlan(
        goal="Build project",
        description="Build and deploy",
        steps=simple_steps,
        priority=Priority.HIGH,
    )


@pytest.fixture
def planner_instance() -> TaskPlanner:
    return TaskPlanner()


@pytest.fixture
def validator_instance() -> PlanValidator:
    return PlanValidator()


@pytest.fixture
def serializer_instance() -> PlanSerializer:
    return PlanSerializer()


@pytest.fixture
def metrics_instance() -> PlannerMetrics:
    return PlannerMetrics()


# ===================================================================
# ENUMS
# ===================================================================


class TestPlanStatus:
    """Tests for PlanStatus enum."""

    def test_plan_status_values_are_strings(self) -> None:
        for status in PlanStatus:
            assert isinstance(status.value, str)

    def test_plan_status_all_values(self) -> None:
        expected = {
            "not_started",
            "planning",
            "ready",
            "running",
            "completed",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in PlanStatus}
        assert actual == expected

    def test_can_transition_plan_allowed(self) -> None:
        assert can_transition_plan(PlanStatus.NOT_STARTED, PlanStatus.PLANNING) is True
        assert can_transition_plan(PlanStatus.PLANNING, PlanStatus.READY) is True
        assert can_transition_plan(PlanStatus.READY, PlanStatus.RUNNING) is True
        assert can_transition_plan(PlanStatus.RUNNING, PlanStatus.COMPLETED) is True
        assert can_transition_plan(PlanStatus.RUNNING, PlanStatus.FAILED) is True
        assert can_transition_plan(PlanStatus.FAILED, PlanStatus.PLANNING) is True

    def test_can_transition_plan_disallowed(self) -> None:
        assert can_transition_plan(PlanStatus.NOT_STARTED, PlanStatus.RUNNING) is False
        assert can_transition_plan(PlanStatus.COMPLETED, PlanStatus.RUNNING) is False
        assert can_transition_plan(PlanStatus.CANCELLED, PlanStatus.PLANNING) is False
        assert can_transition_plan(PlanStatus.PLANNING, PlanStatus.COMPLETED) is False

    def test_can_transition_plan_same_status(self) -> None:
        assert can_transition_plan(PlanStatus.RUNNING, PlanStatus.RUNNING) is False

    def test_can_transition_plan_cancelled_from_any_active(self) -> None:
        assert can_transition_plan(PlanStatus.NOT_STARTED, PlanStatus.CANCELLED) is True
        assert can_transition_plan(PlanStatus.PLANNING, PlanStatus.CANCELLED) is True
        assert can_transition_plan(PlanStatus.READY, PlanStatus.CANCELLED) is True
        assert can_transition_plan(PlanStatus.RUNNING, PlanStatus.CANCELLED) is True
        assert can_transition_plan(PlanStatus.FAILED, PlanStatus.CANCELLED) is True


class TestStepStatus:
    """Tests for StepStatus enum."""

    def test_step_status_values_are_strings(self) -> None:
        for status in StepStatus:
            assert isinstance(status.value, str)

    def test_step_status_all_values(self) -> None:
        expected = {"pending", "ready", "running", "success", "failed", "skipped"}
        actual = {s.value for s in StepStatus}
        assert actual == expected

    def test_can_transition_step_allowed(self) -> None:
        assert can_transition_step(StepStatus.PENDING, StepStatus.READY) is True
        assert can_transition_step(StepStatus.READY, StepStatus.RUNNING) is True
        assert can_transition_step(StepStatus.RUNNING, StepStatus.SUCCESS) is True
        assert can_transition_step(StepStatus.RUNNING, StepStatus.FAILED) is True
        assert can_transition_step(StepStatus.PENDING, StepStatus.SKIPPED) is True
        assert can_transition_step(StepStatus.FAILED, StepStatus.READY) is True

    def test_can_transition_step_disallowed(self) -> None:
        assert can_transition_step(StepStatus.PENDING, StepStatus.RUNNING) is False
        assert can_transition_step(StepStatus.SUCCESS, StepStatus.PENDING) is False
        assert can_transition_step(StepStatus.SKIPPED, StepStatus.RUNNING) is False
        assert can_transition_step(StepStatus.SUCCESS, StepStatus.FAILED) is False


class TestPriority:
    """Tests for Priority enum."""

    def test_priority_values_are_strings(self) -> None:
        for p in Priority:
            assert isinstance(p.value, str)

    def test_priority_all_values(self) -> None:
        expected = {"low", "normal", "high", "critical"}
        actual = {p.value for p in Priority}
        assert actual == expected

    def test_priority_from_string(self) -> None:
        assert Priority("low") == Priority.LOW
        assert Priority("critical") == Priority.CRITICAL
        assert Priority("normal") == Priority.NORMAL

    def test_priority_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            Priority("urgent")


# ===================================================================
# MODELS
# ===================================================================


class TestPlanStep:
    """Tests for PlanStep model."""

    def test_step_defaults(self) -> None:
        step = PlanStep()
        assert step.title == ""
        assert step.description == ""
        assert step.estimated_duration == 0.0
        assert step.dependencies == []
        assert step.required_agents == []
        assert step.status == StepStatus.PENDING
        assert step.metadata == {}
        assert isinstance(step.id, str)
        assert len(step.id) == 12

    def test_step_custom_fields(self) -> None:
        step = PlanStep(
            id="custom",
            title="My Step",
            description="Does things",
            estimated_duration=42.5,
            dependencies=["a", "b"],
            required_agents=["coder"],
            status=StepStatus.READY,
            metadata={"key": "val"},
        )
        assert step.id == "custom"
        assert step.title == "My Step"
        assert step.estimated_duration == 42.5
        assert step.dependencies == ["a", "b"]
        assert step.required_agents == ["coder"]
        assert step.status == StepStatus.READY
        assert step.metadata == {"key": "val"}

    def test_step_to_dict(self) -> None:
        step = PlanStep(id="s1", title="T", dependencies=["d1"], metadata={"x": 1})
        d = step.to_dict()
        assert d["id"] == "s1"
        assert d["title"] == "T"
        assert d["dependencies"] == ["d1"]
        assert d["status"] == "pending"
        assert d["metadata"] == {"x": 1}

    def test_step_from_dict(self) -> None:
        data = {
            "id": "abc",
            "title": "Hello",
            "description": "World",
            "estimated_duration": 5.0,
            "dependencies": ["x"],
            "required_agents": ["agent1"],
            "status": "running",
            "metadata": {"foo": "bar"},
        }
        step = PlanStep.from_dict(data)
        assert step.id == "abc"
        assert step.title == "Hello"
        assert step.status == StepStatus.RUNNING
        assert step.metadata == {"foo": "bar"}

    def test_step_from_dict_defaults(self) -> None:
        step = PlanStep.from_dict({})
        assert step.status == StepStatus.PENDING
        assert step.dependencies == []

    def test_step_from_dict_ignores_unknown_keys(self) -> None:
        step = PlanStep.from_dict({"id": "s1", "unknown": "value"})
        assert not hasattr(step, "unknown") or step.id == "s1"

    def test_step_roundtrip_dict(self) -> None:
        original = PlanStep(
            id="rt",
            title="Roundtrip",
            estimated_duration=7.0,
            dependencies=["d"],
            metadata={"n": 3},
        )
        restored = PlanStep.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.estimated_duration == original.estimated_duration
        assert restored.dependencies == original.dependencies
        assert restored.metadata == original.metadata
        assert restored.status == original.status


class TestTaskPlan:
    """Tests for TaskPlan model."""

    def test_plan_defaults(self) -> None:
        plan = TaskPlan()
        assert plan.goal == ""
        assert plan.description == ""
        assert plan.priority == Priority.NORMAL
        assert plan.status == PlanStatus.NOT_STARTED
        assert plan.steps == []
        assert plan.metadata == {}
        assert isinstance(plan.id, str)
        assert len(plan.id) == 12
        assert isinstance(plan.created_at, datetime)
        assert isinstance(plan.updated_at, datetime)

    def test_plan_custom(self) -> None:
        steps = [PlanStep(id="s1", title="First")]
        plan = TaskPlan(
            goal="Deploy",
            description="Deploy to prod",
            priority=Priority.CRITICAL,
            status=PlanStatus.READY,
            steps=steps,
            metadata={"env": "prod"},
        )
        assert plan.goal == "Deploy"
        assert plan.priority == Priority.CRITICAL
        assert len(plan.steps) == 1

    def test_plan_step_by_id_found(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="a", title="A"), PlanStep(id="b", title="B")])
        assert plan.step_by_id("a") is not None
        assert plan.step_by_id("a").title == "A"  # type: ignore[union-attr]

    def test_plan_step_by_id_not_found(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="a", title="A")])
        assert plan.step_by_id("z") is None

    def test_plan_step_ids(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="x"), PlanStep(id="y"), PlanStep(id="z")])
        assert plan.step_ids() == ["x", "y", "z"]

    def test_plan_step_ids_empty(self) -> None:
        assert TaskPlan().step_ids() == []

    def test_plan_touch_updates_timestamp(self) -> None:
        plan = TaskPlan()
        old = plan.updated_at
        plan.touch()
        assert plan.updated_at >= old

    def test_plan_to_dict(self) -> None:
        plan = TaskPlan(
            goal="G",
            steps=[PlanStep(id="s1", title="S1")],
            metadata={"k": "v"},
        )
        d = plan.to_dict()
        assert d["goal"] == "G"
        assert d["priority"] == "normal"
        assert d["status"] == "not_started"
        assert len(d["steps"]) == 1
        assert d["steps"][0]["id"] == "s1"
        assert d["metadata"] == {"k": "v"}
        assert "created_at" in d
        assert "updated_at" in d

    def test_plan_from_dict(self) -> None:
        data = {
            "id": "p1",
            "goal": "Test",
            "description": "A test plan",
            "priority": "high",
            "status": "planning",
            "steps": [
                {"id": "s1", "title": "Step 1", "status": "pending"},
            ],
            "metadata": {"key": "value"},
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T01:00:00+00:00",
        }
        plan = TaskPlan.from_dict(data)
        assert plan.id == "p1"
        assert plan.goal == "Test"
        assert plan.priority == Priority.HIGH
        assert plan.status == PlanStatus.PLANNING
        assert len(plan.steps) == 1
        assert plan.steps[0].id == "s1"
        assert plan.metadata == {"key": "value"}

    def test_plan_from_dict_defaults(self) -> None:
        plan = TaskPlan.from_dict({})
        assert plan.status == PlanStatus.NOT_STARTED
        assert plan.priority == Priority.NORMAL
        assert plan.steps == []

    def test_plan_roundtrip_dict(self) -> None:
        original = TaskPlan(
            goal="Roundtrip",
            steps=[PlanStep(id="s1", title="S1", dependencies=[])],
            metadata={"n": 1},
        )
        restored = TaskPlan.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.goal == original.goal
        assert restored.priority == original.priority
        assert restored.status == original.status
        assert len(restored.steps) == len(original.steps)
        assert restored.steps[0].id == original.steps[0].id


class TestPlanMetrics:
    """Tests for PlanMetrics model."""

    def test_metrics_defaults(self) -> None:
        m = PlanMetrics()
        assert m.planning_time == 0.0
        assert m.estimated_total_duration == 0.0
        assert m.total_steps == 0
        assert m.sequential_steps == 0
        assert m.parallel_steps == 0

    def test_metrics_custom(self) -> None:
        m = PlanMetrics(
            planning_time=1.5,
            estimated_total_duration=100.0,
            total_steps=10,
            sequential_steps=7,
            parallel_steps=3,
        )
        assert m.planning_time == 1.5
        assert m.parallel_steps == 3

    def test_metrics_to_dict(self) -> None:
        m = PlanMetrics(total_steps=5, sequential_steps=3, parallel_steps=2)
        d = m.to_dict()
        assert d["total_steps"] == 5
        assert d["sequential_steps"] == 3
        assert d["parallel_steps"] == 2

    def test_metrics_from_dict(self) -> None:
        m = PlanMetrics.from_dict({"total_steps": 8, "planning_time": 0.5})
        assert m.total_steps == 8
        assert m.planning_time == 0.5

    def test_metrics_roundtrip(self) -> None:
        original = PlanMetrics(
            planning_time=2.0,
            estimated_total_duration=50.0,
            total_steps=5,
            sequential_steps=4,
            parallel_steps=1,
        )
        restored = PlanMetrics.from_dict(original.to_dict())
        assert restored == original


# ===================================================================
# EXCEPTIONS
# ===================================================================


class TestExceptions:
    """Tests for all exception classes."""

    def test_planner_exception_base(self) -> None:
        exc = PlannerException("base error")
        assert str(exc) == "base error"
        assert isinstance(exc, Exception)

    def test_plan_validation_error_with_errors(self) -> None:
        exc = PlanValidationError("validation failed", errors=["err1", "err2"])
        assert exc.errors == ["err1", "err2"]
        assert "validation failed" in str(exc)

    def test_plan_validation_error_no_errors(self) -> None:
        exc = PlanValidationError("fail")
        assert exc.errors == []

    def test_serialization_error(self) -> None:
        exc = SerializationError("bad format")
        assert isinstance(exc, PlannerException)
        assert str(exc) == "bad format"

    def test_dependency_error(self) -> None:
        exc = DependencyError("s1", "missing")
        assert exc.step_id == "s1"
        assert exc.missing_dep == "missing"
        assert isinstance(exc, PlannerException)
        assert "missing" in str(exc)

    def test_circular_dependency_error(self) -> None:
        exc = CircularDependencyError(["a", "b", "c", "a"])
        assert exc.cycle == ["a", "b", "c", "a"]
        assert isinstance(exc, PlannerException)
        assert "Circular" in str(exc)

    def test_duplicate_step_error(self) -> None:
        exc = DuplicateStepError("dup")
        assert exc.step_id == "dup"
        assert isinstance(exc, PlanValidationError)

    def test_invalid_status_error(self) -> None:
        exc = InvalidStatusError("running", "completed", entity="step")
        assert exc.current == "running"
        assert exc.target == "completed"
        assert exc.entity == "step"
        assert isinstance(exc, PlanValidationError)

    def test_invalid_status_error_default_entity(self) -> None:
        exc = InvalidStatusError("a", "b")
        assert exc.entity == "plan"


# ===================================================================
# INTERFACES
# ===================================================================


class TestInterfaces:
    """Tests that interfaces cannot be instantiated and require implementation."""

    def test_iplanner_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            IPlanner()  # type: ignore[abstract]

    def test_ivalidator_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            IValidator()  # type: ignore[abstract]

    def test_iserializer_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            ISerializer()  # type: ignore[abstract]

    def test_imetrics_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            IMetrics()  # type: ignore[abstract]

    def test_concrete_impl_satisfies_iplanner(self) -> None:
        assert isinstance(TaskPlanner(), IPlanner)

    def test_concrete_impl_satisfies_ivalidator(self) -> None:
        assert isinstance(PlanValidator(), IValidator)

    def test_concrete_impl_satisfies_iserializer(self) -> None:
        assert isinstance(PlanSerializer(), ISerializer)

    def test_concrete_impl_satisfies_imetrics(self) -> None:
        assert isinstance(PlannerMetrics(), IMetrics)


# ===================================================================
# VALIDATOR
# ===================================================================


class TestPlanValidator:
    """Tests for PlanValidator."""

    def test_validate_empty_plan_is_valid(self, empty_plan: TaskPlan) -> None:
        errors = PlanValidator().validate(empty_plan)
        assert errors == []

    def test_validate_valid_plan_no_errors(self, plan_with_steps: TaskPlan) -> None:
        errors = PlanValidator().validate(plan_with_steps)
        assert errors == []

    def test_validate_duplicate_step_ids(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="dup", title="A"),
                PlanStep(id="dup", title="B"),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("Duplicate" in e for e in errors)

    def test_validate_missing_dependency(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="s1", title="A", dependencies=["nonexistent"]),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("nonexistent" in e for e in errors)

    def test_validate_circular_dependency(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["c"]),
                PlanStep(id="b", title="B", dependencies=["a"]),
                PlanStep(id="c", title="C", dependencies=["b"]),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("Circular" in e or "circular" in e for e in errors)

    def test_validate_self_dependency_circular(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["a"]),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("Circular" in e or "circular" in e for e in errors)

    def test_validate_bad_metadata_type(self) -> None:
        plan = TaskPlan(metadata={"bad": object()})
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("non-serializable" in e for e in errors)

    def test_validate_step_bad_metadata_type(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="s1", title="S", metadata={"x": set()}),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert any("non-serializable" in e for e in errors)

    def test_validate_valid_metadata_types(self) -> None:
        plan = TaskPlan(
            metadata={
                "str": "a",
                "int": 1,
                "float": 1.0,
                "bool": True,
                "none": None,
                "list": [1, 2],
                "dict": {"k": "v"},
            },
            steps=[PlanStep(id="s1", title="S", metadata={"a": [1, 2]})],
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert not any("non-serializable" in e for e in errors)

    def test_validate_or_raise_valid(self, plan_with_steps: TaskPlan) -> None:
        v = PlanValidator()
        v.validate_or_raise(plan_with_steps)  # should not raise

    def test_validate_or_raise_duplicate(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="dup", title="A"),
                PlanStep(id="dup", title="B"),
            ]
        )
        v = PlanValidator()
        with pytest.raises(DuplicateStepError):
            v.validate_or_raise(plan)

    def test_validate_or_raise_missing_dep(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="s1", title="A", dependencies=["missing"]),
            ]
        )
        v = PlanValidator()
        with pytest.raises(DependencyError) as exc_info:
            v.validate_or_raise(plan)
        assert exc_info.value.step_id == "s1"
        assert exc_info.value.missing_dep == "missing"

    def test_validate_or_raise_circular(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["b"]),
                PlanStep(id="b", title="B", dependencies=["a"]),
            ]
        )
        v = PlanValidator()
        with pytest.raises(CircularDependencyError) as exc_info:
            v.validate_or_raise(plan)
        assert len(exc_info.value.cycle) > 0

    def test_validate_plan_status_transition_valid(self) -> None:
        PlanValidator.validate_plan_status_transition(PlanStatus.NOT_STARTED, PlanStatus.PLANNING)

    def test_validate_plan_status_transition_invalid(self) -> None:
        with pytest.raises(InvalidStatusError):
            PlanValidator.validate_plan_status_transition(
                PlanStatus.NOT_STARTED, PlanStatus.COMPLETED
            )

    def test_validate_step_status_transition_valid(self) -> None:
        PlanValidator.validate_step_status_transition(StepStatus.PENDING, StepStatus.READY)

    def test_validate_step_status_transition_invalid(self) -> None:
        with pytest.raises(InvalidStatusError):
            PlanValidator.validate_step_status_transition(StepStatus.SUCCESS, StepStatus.PENDING)

    def test_validate_no_errors_on_valid_parallel_plan(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B"),
                PlanStep(id="c", title="C"),
            ]
        )
        errors = PlanValidator().validate(plan)
        assert errors == []

    def test_validate_complex_dag_no_cycle(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B", dependencies=["a"]),
                PlanStep(id="c", title="C", dependencies=["a"]),
                PlanStep(id="d", title="D", dependencies=["b", "c"]),
            ]
        )
        errors = PlanValidator().validate(plan)
        assert errors == []

    def test_validate_multiple_errors(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="dup", title="A", dependencies=["missing"]),
                PlanStep(id="dup", title="B"),
            ]
        )
        v = PlanValidator()
        errors = v.validate(plan)
        assert len(errors) >= 2


# ===================================================================
# SERIALIZER
# ===================================================================


class TestPlanSerializer:
    """Tests for PlanSerializer."""

    def test_serialize_to_dict(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        result = s.serialize(plan_with_steps, format="dict")
        assert isinstance(result, dict)
        assert result["goal"] == "Build project"
        assert len(result["steps"]) == 3

    def test_serialize_to_json(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        result = s.serialize(plan_with_steps, format="json")
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["goal"] == "Build project"

    def test_serialize_unsupported_format(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Unsupported"):
            s.serialize(plan_with_steps, format="xml")

    def test_deserialize_from_dict(self) -> None:
        data = {
            "id": "p1",
            "goal": "Test",
            "priority": "high",
            "status": "not_started",
            "steps": [{"id": "s1", "title": "S1", "status": "pending"}],
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }
        s = PlanSerializer()
        plan = s.deserialize(data, format="dict")
        assert plan.id == "p1"
        assert plan.goal == "Test"
        assert plan.priority == Priority.HIGH

    def test_deserialize_from_json(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        json_str = s.serialize(plan_with_steps, format="json")
        restored = s.deserialize(json_str, format="json")
        assert restored.goal == plan_with_steps.goal
        assert len(restored.steps) == len(plan_with_steps.steps)

    def test_deserialize_json_string_as_dict_format(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        json_str = s.serialize(plan_with_steps, format="json")
        # format="dict" should auto-parse JSON strings
        restored = s.deserialize(json_str, format="dict")
        assert restored.goal == plan_with_steps.goal

    def test_deserialize_invalid_json(self) -> None:
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Failed to parse"):
            s.deserialize("not valid json {{{", format="dict")

    def test_deserialize_invalid_json_format(self) -> None:
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Invalid JSON"):
            s.deserialize("{bad}", format="json")

    def test_deserialize_unsupported_format(self) -> None:
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Unsupported"):
            s.deserialize({}, format="xml")

    def test_deserialize_wrong_type_for_dict(self) -> None:
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Expected dict"):
            s.deserialize(42, format="dict")

    def test_serialize_deserialize_roundtrip_dict(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        data = s.serialize(plan_with_steps, format="dict")
        restored = s.deserialize(data, format="dict")
        assert restored.goal == plan_with_steps.goal
        assert restored.id == plan_with_steps.id
        assert len(restored.steps) == len(plan_with_steps.steps)
        for orig, rest in zip(plan_with_steps.steps, restored.steps):
            assert orig.id == rest.id
            assert orig.title == rest.title

    def test_serialize_deserialize_roundtrip_json(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        json_str = s.serialize(plan_with_steps, format="json")
        restored = s.deserialize(json_str, format="json")
        assert restored.goal == plan_with_steps.goal
        assert len(restored.steps) == len(plan_with_steps.steps)

    def test_yaml_not_available_raises(self, plan_with_steps: TaskPlan) -> None:
        s = PlanSerializer()
        with patch.dict("sys.modules", {"yaml": None}):
            # Force re-import failure by patching the method
            with pytest.raises(SerializationError, match="PyYAML"):
                s.serialize(plan_with_steps, format="yaml")

    def test_deserialize_yaml_not_available_raises(self) -> None:
        s = PlanSerializer()
        with patch.dict("sys.modules", {"yaml": None}):
            with pytest.raises(SerializationError, match="PyYAML"):
                s.deserialize("key: val", format="yaml")


# ===================================================================
# METRICS
# ===================================================================


class TestPlannerMetrics:
    """Tests for PlannerMetrics."""

    def test_compute_empty_plan(self, empty_plan: TaskPlan) -> None:
        m = PlannerMetrics().compute(empty_plan)
        assert m.total_steps == 0
        assert m.estimated_total_duration == 0.0
        assert m.sequential_steps == 0
        assert m.parallel_steps == 0

    def test_compute_with_steps(self, plan_with_steps: TaskPlan) -> None:
        m = PlannerMetrics().compute(plan_with_steps)
        assert m.total_steps == 3
        assert m.estimated_total_duration == 35.0  # 10 + 20 + 5
        assert m.sequential_steps == 2  # s2 and s3 depend on s1
        assert m.parallel_steps == 1  # s1 has no deps

    def test_compute_with_planning_time(self, plan_with_steps: TaskPlan) -> None:
        m = PlannerMetrics().compute(plan_with_steps, planning_time=1.23)
        assert m.planning_time == 1.23

    def test_depth_empty_plan(self, empty_plan: TaskPlan) -> None:
        assert PlannerMetrics().depth(empty_plan) == 0

    def test_depth_no_dependencies(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b"),
                PlanStep(id="c"),
            ]
        )
        assert PlannerMetrics().depth(plan) == 0

    def test_depth_linear_chain(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b", dependencies=["a"]),
                PlanStep(id="c", dependencies=["b"]),
            ]
        )
        assert PlannerMetrics().depth(plan) == 2

    def test_depth_diamond(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b", dependencies=["a"]),
                PlanStep(id="c", dependencies=["a"]),
                PlanStep(id="d", dependencies=["b", "c"]),
            ]
        )
        assert PlannerMetrics().depth(plan) == 2

    def test_dependency_count(self, plan_with_steps: TaskPlan) -> None:
        assert PlannerMetrics().dependency_count(plan_with_steps) == 2  # s2->s1, s3->s1

    def test_dependency_count_empty(self, empty_plan: TaskPlan) -> None:
        assert PlannerMetrics().dependency_count(empty_plan) == 0

    def test_complexity_empty(self, empty_plan: TaskPlan) -> None:
        assert PlannerMetrics().complexity(empty_plan) == 0.0

    def test_complexity_single_step(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="a")])
        c = PlannerMetrics().complexity(plan)
        assert 0.0 <= c <= 1.5

    def test_complexity_linear_is_higher_than_parallel(self) -> None:
        # Linear: a -> b -> c -> d -> e
        linear = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b", dependencies=["a"]),
                PlanStep(id="c", dependencies=["b"]),
                PlanStep(id="d", dependencies=["c"]),
                PlanStep(id="e", dependencies=["d"]),
            ]
        )
        # Parallel: a, b, c, d, e (no deps)
        parallel = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b"),
                PlanStep(id="c"),
                PlanStep(id="d"),
                PlanStep(id="e"),
            ]
        )
        mc = PlannerMetrics()
        assert mc.complexity(linear) > mc.complexity(parallel)

    def test_critical_path_empty(self, empty_plan: TaskPlan) -> None:
        assert PlannerMetrics().critical_path(empty_plan) == []

    def test_critical_path_no_deps(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", estimated_duration=5),
                PlanStep(id="b", estimated_duration=10),
            ]
        )
        path = PlannerMetrics().critical_path(plan)
        # The longest single step should be the path
        assert len(path) == 1
        assert path[0] == "b"

    def test_critical_path_linear(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", estimated_duration=5),
                PlanStep(id="b", estimated_duration=10, dependencies=["a"]),
                PlanStep(id="c", estimated_duration=3, dependencies=["b"]),
            ]
        )
        path = PlannerMetrics().critical_path(plan)
        assert path == ["a", "b", "c"]

    def test_critical_path_diamond(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", estimated_duration=5),
                PlanStep(id="b", estimated_duration=10, dependencies=["a"]),
                PlanStep(id="c", estimated_duration=3, dependencies=["a"]),
                PlanStep(id="d", estimated_duration=2, dependencies=["b", "c"]),
            ]
        )
        path = PlannerMetrics().critical_path(plan)
        assert path[0] == "a"
        assert path[-1] == "d"
        assert "b" in path

    def test_ready_steps_empty_completed(self, plan_with_steps: TaskPlan) -> None:
        ready = PlannerMetrics().ready_steps(plan_with_steps, completed=set())
        # Only s1 has no deps
        assert "s1" in ready
        assert "s2" not in ready
        assert "s3" not in ready

    def test_ready_steps_after_completion(self, plan_with_steps: TaskPlan) -> None:
        ready = PlannerMetrics().ready_steps(plan_with_steps, completed={"s1"})
        assert "s2" in ready
        assert "s3" in ready

    def test_ready_steps_none_completed_defaults_to_empty(self, plan_with_steps: TaskPlan) -> None:
        ready = PlannerMetrics().ready_steps(plan_with_steps)
        assert "s1" in ready


# ===================================================================
# TASK PLANNER (integration)
# ===================================================================


class TestTaskPlanner:
    """Tests for the main TaskPlanner class."""

    def test_create_plan_minimal(self, planner_instance: TaskPlanner) -> None:
        plan = planner_instance.create_plan(goal="Do X", steps=[])
        assert plan.goal == "Do X"
        assert plan.status == PlanStatus.NOT_STARTED
        assert plan.priority == Priority.NORMAL
        assert plan.steps == []

    def test_create_plan_with_all_params(self, planner_instance: TaskPlanner) -> None:
        steps = [PlanStep(id="s1", title="S1")]
        plan = planner_instance.create_plan(
            goal="Full plan",
            steps=steps,
            description="A full plan",
            priority="high",
            metadata={"env": "test"},
        )
        assert plan.goal == "Full plan"
        assert plan.description == "A full plan"
        assert plan.priority == Priority.HIGH
        assert plan.metadata == {"env": "test"}
        assert len(plan.steps) == 1

    def test_create_plan_default_status(self, planner_instance: TaskPlanner) -> None:
        plan = planner_instance.create_plan(goal="G", steps=[])
        assert plan.status == PlanStatus.NOT_STARTED

    def test_clone_creates_new_id(
        self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan
    ) -> None:
        cloned = planner_instance.clone(plan_with_steps)
        assert cloned.id != plan_with_steps.id

    def test_clone_resets_status(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(
            goal="G",
            steps=[PlanStep(id="s1", title="S1", status=StepStatus.RUNNING)],
            status=PlanStatus.RUNNING,
        )
        cloned = planner_instance.clone(plan)
        assert cloned.status == PlanStatus.NOT_STARTED
        assert cloned.steps[0].status == StepStatus.PENDING

    def test_clone_new_step_ids(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(steps=[PlanStep(id="original", title="O")])
        cloned = planner_instance.clone(plan)
        assert cloned.steps[0].id != "original"

    def test_clone_independent_mutation(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(steps=[PlanStep(id="s1", title="S1")])
        cloned = planner_instance.clone(plan)
        cloned.goal = "Modified"
        assert plan.goal != "Modified"

    def test_validate_valid(self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan) -> None:
        errors = planner_instance.validate(plan_with_steps)
        assert errors == []

    def test_validate_invalid(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["missing"]),
            ]
        )
        errors = planner_instance.validate(plan)
        assert len(errors) > 0

    def test_validate_or_raise_valid(
        self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan
    ) -> None:
        planner_instance.validate_or_raise(plan_with_steps)  # should not raise

    def test_validate_or_raise_invalid(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["missing"]),
            ]
        )
        with pytest.raises(PlannerException):
            planner_instance.validate_or_raise(plan)

    def test_estimate_returns_metrics(
        self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan
    ) -> None:
        metrics = planner_instance.estimate(plan_with_steps)
        assert isinstance(metrics, PlanMetrics)
        assert metrics.total_steps == 3
        assert metrics.planning_time >= 0.0

    def test_estimate_includes_planning_time(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(goal="G", steps=[PlanStep(id="s1", title="S1", estimated_duration=10)])
        metrics = planner_instance.estimate(plan)
        assert metrics.planning_time > 0.0
        assert metrics.estimated_total_duration == 10.0

    def test_serialize_dict(self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan) -> None:
        data = planner_instance.serialize(plan_with_steps, format="dict")
        assert isinstance(data, dict)
        assert data["goal"] == "Build project"

    def test_serialize_json(self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan) -> None:
        data = planner_instance.serialize(plan_with_steps, format="json")
        assert isinstance(data, str)
        parsed = json.loads(data)
        assert parsed["goal"] == "Build project"

    def test_deserialize_dict(
        self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan
    ) -> None:
        data = planner_instance.serialize(plan_with_steps, format="dict")
        restored = planner_instance.deserialize(data, format="dict")
        assert restored.goal == plan_with_steps.goal

    def test_deserialize_json(
        self, planner_instance: TaskPlanner, plan_with_steps: TaskPlan
    ) -> None:
        data = planner_instance.serialize(plan_with_steps, format="json")
        restored = planner_instance.deserialize(data, format="json")
        assert restored.goal == plan_with_steps.goal

    def test_properties(self, planner_instance: TaskPlanner) -> None:
        assert isinstance(planner_instance.validator, IValidator)
        assert isinstance(planner_instance.serializer, ISerializer)
        assert isinstance(planner_instance.metrics, IMetrics)

    def test_custom_validator(self) -> None:
        custom = MagicMock(spec=IValidator)
        custom.validate.return_value = ["custom error"]
        p = TaskPlanner(validator=custom)
        plan = TaskPlan(goal="G", steps=[])
        assert p.validate(plan) == ["custom error"]

    def test_custom_serializer(self, plan_with_steps: TaskPlan) -> None:
        custom = MagicMock(spec=ISerializer)
        custom.serialize.return_value = {"custom": True}
        p = TaskPlanner(serializer=custom)
        result = p.serialize(plan_with_steps)
        assert result == {"custom": True}

    def test_custom_metrics(self, empty_plan: TaskPlan) -> None:
        custom_metrics = PlanMetrics(total_steps=99, sequential_steps=50, parallel_steps=49)
        custom = MagicMock(spec=IMetrics)
        custom.compute.return_value = custom_metrics
        p = TaskPlanner(metrics=custom)
        result = p.estimate(empty_plan)
        assert result.total_steps == 99

    def test_validate_or_raise_custom_validator(self) -> None:
        custom = MagicMock(spec=IValidator)
        custom.validate.return_value = ["err"]
        p = TaskPlanner(validator=custom)
        plan = TaskPlan(goal="G", steps=[])
        with pytest.raises(PlanValidationError):
            p.validate_or_raise(plan)


# ===================================================================
# YAML INTEGRATION (if available)
# ===================================================================


class TestYamlIntegration:
    """Tests that run only when PyYAML is installed."""

    def test_serialize_yaml_when_available(self, plan_with_steps: TaskPlan) -> None:
        yaml = pytest.importorskip("yaml")
        s = PlanSerializer()
        result = s.serialize(plan_with_steps, format="yaml")
        assert isinstance(result, str)
        parsed = yaml.safe_load(result)
        assert parsed["goal"] == "Build project"

    def test_deserialize_yaml_when_available(self, plan_with_steps: TaskPlan) -> None:
        pytest.importorskip("yaml")
        s = PlanSerializer()
        yaml_str = s.serialize(plan_with_steps, format="yaml")
        restored = s.deserialize(yaml_str, format="yaml")
        assert restored.goal == plan_with_steps.goal
        assert len(restored.steps) == len(plan_with_steps.steps)

    def test_yaml_roundtrip(self, plan_with_steps: TaskPlan) -> None:
        pytest.importorskip("yaml")
        p = TaskPlanner()
        yaml_str = p.serialize(plan_with_steps, format="yaml")
        restored = p.deserialize(yaml_str, format="yaml")
        assert restored.id == plan_with_steps.id
        assert restored.goal == plan_with_steps.goal


# ===================================================================
# EDGE CASES & ADDITIONAL COVERAGE
# ===================================================================


class TestEdgeCases:
    """Additional tests for edge cases and branch coverage."""

    def test_plan_with_many_steps(self) -> None:
        steps = [PlanStep(id=f"s{i}", title=f"Step {i}") for i in range(100)]
        plan = TaskPlan(goal="Big plan", steps=steps)
        assert len(plan.steps) == 100
        assert plan.step_ids() == [f"s{i}" for i in range(100)]

    def test_step_with_all_metadata_types(self) -> None:
        step = PlanStep(
            id="meta",
            title="Metadata",
            metadata={
                "str": "hello",
                "int": 42,
                "float": 3.14,
                "bool": True,
                "none": None,
                "list": [1, 2, 3],
                "dict": {"nested": True},
            },
        )
        d = step.to_dict()
        restored = PlanStep.from_dict(d)
        assert restored.metadata["str"] == "hello"
        assert restored.metadata["list"] == [1, 2, 3]

    def test_empty_goal(self) -> None:
        plan = TaskPlan(goal="")
        assert plan.goal == ""

    def test_unicode_content(self) -> None:
        plan = TaskPlan(
            goal="Desplegar producción",
            description="Planificación en español",
            steps=[
                PlanStep(id="s1", title="Paso 1: Preparar", description="Configuración inicial")
            ],
        )
        data = plan.to_dict()
        assert "Desplegar" in data["goal"]
        restored = TaskPlan.from_dict(data)
        assert "español" in restored.description

    def test_plan_serializer_json_with_unicode(self) -> None:
        plan = TaskPlan(goal="Tarea con ñandú y 🎉")
        s = PlanSerializer()
        json_str = s.serialize(plan, format="json")
        assert "ñandú" in json_str

    def test_deserialize_json_as_dict_produces_plan(self) -> None:
        s = PlanSerializer()
        json_data = '{"id": "p1", "goal": "G", "priority": "normal", "status": "not_started", "steps": [], "metadata": {}, "created_at": "2025-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"}'
        plan = s.deserialize(json_data, format="json")
        assert plan.id == "p1"

    def test_plan_from_dict_missing_steps_key(self) -> None:
        plan = TaskPlan.from_dict({"id": "p1", "goal": "G"})
        assert plan.steps == []

    def test_metrics_ready_steps_with_no_steps(self, empty_plan: TaskPlan) -> None:
        ready = PlannerMetrics().ready_steps(empty_plan)
        assert ready == []

    def test_metrics_critical_path_single_step(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="only", estimated_duration=5)])
        path = PlannerMetrics().critical_path(plan)
        assert path == ["only"]

    def test_clone_empty_plan(self, planner_instance: TaskPlanner) -> None:
        original = TaskPlan(goal="Empty")
        cloned = planner_instance.clone(original)
        assert cloned.goal == "Empty"
        assert cloned.id != original.id
        assert cloned.steps == []

    def test_clone_preserves_dependencies(self, planner_instance: TaskPlanner) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B", dependencies=["a"]),
            ]
        )
        cloned = planner_instance.clone(plan)
        # Step IDs changed but dependency structure preserved via new IDs
        assert len(cloned.steps) == 2
        assert len(cloned.steps[1].dependencies) == 1

    def test_validator_extract_cycle_no_cycle(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B", dependencies=["a"]),
            ]
        )
        cycle = PlanValidator._extract_cycle(plan)
        assert cycle == []

    def test_serializer_from_yaml_invalid_raises(self) -> None:
        pytest.importorskip("yaml")
        s = PlanSerializer()
        with pytest.raises(SerializationError):
            s._from_yaml(": invalid\n  yaml: [")

    def test_serializer_from_yaml_non_mapping_raises(self) -> None:
        pytest.importorskip("yaml")
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="must be a mapping"):
            s._from_yaml("- item1\n- item2\n")

    def test_plan_to_dict_contains_iso_timestamps(self) -> None:
        plan = TaskPlan(goal="T")
        d = plan.to_dict()
        # Should be parseable
        datetime.fromisoformat(d["created_at"])
        datetime.fromisoformat(d["updated_at"])

    def test_plan_from_dict_without_timestamps(self) -> None:
        plan = TaskPlan.from_dict({"goal": "G", "steps": []})
        assert isinstance(plan.created_at, datetime)
        assert isinstance(plan.updated_at, datetime)

    def test_create_plan_with_priority_enum(self, planner_instance: TaskPlanner) -> None:
        plan = planner_instance.create_plan(
            goal="G",
            steps=[],
            priority=Priority.CRITICAL,
        )
        assert plan.priority == Priority.CRITICAL

    def test_create_plan_with_priority_string(self, planner_instance: TaskPlanner) -> None:
        plan = planner_instance.create_plan(
            goal="G",
            steps=[],
            priority="low",
        )
        assert plan.priority == Priority.LOW

    def test_all_exceptions_inherit_from_planner_exception(self) -> None:
        assert issubclass(PlanValidationError, PlannerException)
        assert issubclass(SerializationError, PlannerException)
        assert issubclass(DependencyError, PlannerException)
        assert issubclass(CircularDependencyError, PlannerException)
        assert issubclass(DuplicateStepError, PlannerException)
        assert issubclass(InvalidStatusError, PlannerException)

    def test_plan_touch_is_recent(self) -> None:
        before = datetime.now(timezone.utc)
        plan = TaskPlan()
        plan.touch()
        after = datetime.now(timezone.utc)
        assert before <= plan.updated_at <= after

    def test_step_to_dict_preserves_agents(self) -> None:
        step = PlanStep(
            id="s1",
            title="S",
            required_agents=["coder", "reviewer"],
        )
        d = step.to_dict()
        assert d["required_agents"] == ["coder", "reviewer"]

    def test_step_from_dict_preserves_agents(self) -> None:
        step = PlanStep.from_dict(
            {
                "id": "s1",
                "title": "S",
                "required_agents": ["git-agent"],
            }
        )
        assert step.required_agents == ["git-agent"]

    def test_metrics_complexity_increases_with_density(self) -> None:
        # Sparse plan
        sparse = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b"),
                PlanStep(id="c"),
                PlanStep(id="d"),
            ]
        )
        # Dense plan (fully connected chain)
        dense = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b", dependencies=["a"]),
                PlanStep(id="c", dependencies=["a", "b"]),
                PlanStep(id="d", dependencies=["a", "b", "c"]),
            ]
        )
        mc = PlannerMetrics()
        assert mc.complexity(dense) > mc.complexity(sparse)

    def test_deserialize_json_accepts_dict_input(self, plan_with_steps: TaskPlan) -> None:
        """JSON format should accept a dict directly."""
        s = PlanSerializer()
        data = plan_with_steps.to_dict()
        plan = s.deserialize(data, format="json")
        assert plan.id == plan_with_steps.id

    def test_plan_step_ids_preserves_order(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="c", title="C"),
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B"),
            ]
        )
        assert plan.step_ids() == ["c", "a", "b"]

    def test_validate_with_isolated_step(self) -> None:
        """A step referencing a dependency not in the plan."""
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A"),
                PlanStep(id="b", title="B", dependencies=["z"]),
            ]
        )
        errors = PlanValidator().validate(plan)
        assert any("z" in e for e in errors)

    def test_complexity_uses_log_normalization(self) -> None:
        """Plans with >20 steps should be capped at 1.0 for the step factor."""
        steps = [PlanStep(id=f"s{i}") for i in range(100)]
        plan = TaskPlan(goal="Large", steps=steps)
        c = PlannerMetrics().complexity(plan)
        assert c >= 0.0
        assert c <= 2.0  # density could push it above 1.0

    def test_ready_steps_returns_all_when_no_deps(self) -> None:
        plan = TaskPlan(
            steps=[
                PlanStep(id="a"),
                PlanStep(id="b"),
                PlanStep(id="c"),
            ]
        )
        ready = PlannerMetrics().ready_steps(plan, completed=set())
        assert set(ready) == {"a", "b", "c"}

    def test_validate_deep_chain_no_cycle(self) -> None:
        steps = []
        for i in range(20):
            deps = [f"s{i-1}"] if i > 0 else []
            steps.append(PlanStep(id=f"s{i}", title=f"Step {i}", dependencies=deps))
        plan = TaskPlan(steps=steps)
        errors = PlanValidator().validate(plan)
        assert errors == []

    def test_plan_with_empty_string_id(self) -> None:
        plan = TaskPlan(steps=[PlanStep(id="", title="Empty ID")])
        # Two empty IDs = duplicate
        plan.steps.append(PlanStep(id="", title="Also empty"))
        errors = PlanValidator().validate(plan)
        assert any("Duplicate" in e for e in errors)

    def test_validate_invalid_plan_status_type(self) -> None:
        """Cover validator line 69: plan.status is not a PlanStatus."""
        plan = TaskPlan(goal="G")
        plan.status = "not_a_status"  # type: ignore[assignment]
        errors = PlanValidator().validate(plan)
        assert any("Invalid plan status" in e for e in errors)

    def test_validate_invalid_step_status_type(self) -> None:
        """Cover validator line 78: step.status is not a StepStatus."""
        step = PlanStep(id="s1", title="S")
        step.status = "invalid"  # type: ignore[assignment]
        plan = TaskPlan(steps=[step])
        errors = PlanValidator().validate(plan)
        assert any("invalid status" in e.lower() for e in errors)

    def test_validate_or_raise_general_fallback(self) -> None:
        """Cover validator line 194: general validation error path."""
        plan = TaskPlan(goal="G")
        plan.status = "bad"  # type: ignore[assignment]
        with pytest.raises(PlanValidationError) as exc_info:
            PlanValidator().validate_or_raise(plan)
        assert len(exc_info.value.errors) > 0

    def test_serializer_deserialize_json_wrong_type(self) -> None:
        """Cover serializer line 98: non-str, non-dict for json format."""
        s = PlanSerializer()
        with pytest.raises(SerializationError, match="Expected str or dict"):
            s.deserialize(42, format="json")

    def test_metrics_critical_path_empty_cum(self) -> None:
        """Cover metrics line 151: empty cumulative dict (edge case)."""
        # Steps with no durations and no valid deps — the cumulative dict
        # will exist but this tests the safety guard at line 168
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", estimated_duration=0),
                PlanStep(id="b", estimated_duration=0, dependencies=["a"]),
                PlanStep(id="c", estimated_duration=0, dependencies=["b"]),
                PlanStep(id="d", estimated_duration=0, dependencies=["c"]),
            ]
        )
        path = PlannerMetrics().critical_path(plan)
        assert isinstance(path, list)

    def test_validate_or_raise_with_cycle_extracts_cycle(self) -> None:
        """Cover validator lines 214-215: cycle extraction backtracking."""
        plan = TaskPlan(
            steps=[
                PlanStep(id="a", title="A", dependencies=["c"]),
                PlanStep(id="b", title="B", dependencies=["a"]),
                PlanStep(id="c", title="C", dependencies=["b"]),
            ]
        )
        v = PlanValidator()
        with pytest.raises(CircularDependencyError) as exc_info:
            v.validate_or_raise(plan)
        # The cycle should have at least 3 members
        assert len(exc_info.value.cycle) >= 3
