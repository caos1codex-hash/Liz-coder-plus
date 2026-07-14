"""Tests for pipeline models: TaskRequest, TaskResult, WorkflowStepDef, StepRetryPolicy, PipelineWorkflow."""

import pytest

from packages.multiagent.src.multiagent.workflow.pipeline_models import (
    PipelineWorkflow,
    StepRetryPolicy,
    TaskRequest,
    TaskResult,
    WorkflowStepDef,
)


# ======================================================================
# TaskRequest tests
# ======================================================================


class TestTaskRequest:
    """Tests for TaskRequest model."""

    def test_create_minimal(self):
        req = TaskRequest(description="Generate code", capability="code.generate")
        assert req.description == "Generate code"
        assert req.capability == "code.generate"
        assert req.task_id
        assert req.context == {}
        assert req.metadata == {}
        assert req.payload == {}

    def test_create_full(self):
        req = TaskRequest(
            description="Run tests",
            capability="code.test",
            context={"prev_output": "file.py"},
            metadata={"priority": 1, "workflow_id": "wf-1"},
            payload={"files": ["a.py"]},
        )
        assert req.context["prev_output"] == "file.py"
        assert req.metadata["priority"] == 1
        assert req.payload["files"] == ["a.py"]

    def test_to_agent_task(self):
        req = TaskRequest(
            description="Test",
            capability="code.test",
            metadata={"priority": 5, "workflow_id": "w1", "step_id": "s1"},
            context={"key": "val"},
        )
        task = req.to_agent_task()
        assert task["name"] == "Test"
        assert task["payload"]["capability"] == "code.test"
        assert task["priority"] == 5
        assert task["workflow_id"] == "w1"
        assert task["step_id"] == "s1"
        assert task["context"]["key"] == "val"

    def test_to_dict_roundtrip(self):
        req = TaskRequest(description="Desc", capability="cap", context={"a": 1})
        data = req.to_dict()
        restored = TaskRequest.from_dict(data)
        assert restored.description == "Desc"
        assert restored.capability == "cap"
        assert restored.context == {"a": 1}
        assert restored.task_id == req.task_id

    def test_from_dict_defaults(self):
        data = {"description": "D", "capability": "C"}
        req = TaskRequest.from_dict(data)
        assert req.task_id
        assert req.context == {}
        assert req.metadata == {}
        assert req.payload == {}


# ======================================================================
# TaskResult tests
# ======================================================================


class TestTaskResult:
    """Tests for TaskResult model."""

    def test_ok_factory(self):
        result = TaskResult.ok(
            output={"files": ["a.py"]},
            execution_time=42.5,
            agent_id="a1",
            agent_name="coder",
            metrics={"tokens_prompt": 10},
            task_id="t1",
            step_id="s1",
        )
        assert result.success is True
        assert result.output == {"files": ["a.py"]}
        assert result.execution_time == 42.5
        assert result.agent_name == "coder"
        assert result.metrics["tokens_prompt"] == 10
        assert result.error is None

    def test_fail_factory(self):
        result = TaskResult.fail(error="timeout", agent_name="coder", step_id="s1")
        assert result.success is False
        assert result.error == "timeout"
        assert result.agent_name == "coder"

    def test_to_dict(self):
        result = TaskResult.ok(output="data", agent_name="a")
        data = result.to_dict()
        assert data["success"] is True
        assert data["output"] == "data"
        assert data["agent_name"] == "a"
        assert "execution_time" in data

    def test_from_dict_roundtrip(self):
        result = TaskResult.ok(output=[1, 2], execution_time=10.0, agent_id="id1")
        data = result.to_dict()
        restored = TaskResult.from_dict(data)
        assert restored.success is True
        assert restored.output == [1, 2]
        assert restored.execution_time == 10.0
        assert restored.agent_id == "id1"

    def test_to_dict_non_jsonable_output(self):
        class Custom:
            pass
        result = TaskResult.ok(output=Custom())
        data = result.to_dict()
        assert "Custom" in data["output"]

    def test_default_values(self):
        result = TaskResult()
        assert result.success is False
        assert result.output is None
        assert result.error is None
        assert result.execution_time == 0.0
        assert result.agent_id == ""
        assert result.agent_name == ""
        assert result.metrics == {}


# ======================================================================
# StepRetryPolicy tests
# ======================================================================


class TestStepRetryPolicy:
    """Tests for StepRetryPolicy model."""

    def test_should_retry_any_error(self):
        policy = StepRetryPolicy(max_attempts=3, retry_delay=1.0)
        assert policy.should_retry("some error") is True
        assert policy.should_retry("timeout") is True
        assert policy.should_retry(None) is False
        assert policy.should_retry("") is False

    def test_should_retry_specific_errors(self):
        policy = StepRetryPolicy(
            max_attempts=3,
            retry_on_errors=["timeout", "connection"],
        )
        assert policy.should_retry("Request timeout after 30s") is True
        assert policy.should_retry("Connection refused") is True
        assert policy.should_retry("syntax error") is False
        assert policy.should_retry(None) is False

    def test_should_retry_case_insensitive(self):
        policy = StepRetryPolicy(retry_on_errors=["Timeout"])
        assert policy.should_retry("timeout") is True
        assert policy.should_retry("TIMEOUT ERROR") is True

    def test_to_dict(self):
        policy = StepRetryPolicy(max_attempts=5, retry_delay=2.0, retry_on_errors=["err"])
        data = policy.to_dict()
        assert data["max_attempts"] == 5
        assert data["retry_delay"] == 2.0
        assert data["retry_on_errors"] == ["err"]

    def test_from_dict_roundtrip(self):
        policy = StepRetryPolicy(max_attempts=3, retry_delay=0.5, retry_on_errors=["a", "b"])
        data = policy.to_dict()
        restored = StepRetryPolicy.from_dict(data)
        assert restored.max_attempts == 3
        assert restored.retry_delay == 0.5
        assert restored.retry_on_errors == ["a", "b"]

    def test_from_dict_defaults(self):
        restored = StepRetryPolicy.from_dict({})
        assert restored.max_attempts == 3
        assert restored.retry_delay == 1.0
        assert restored.retry_on_errors == []


# ======================================================================
# WorkflowStepDef tests
# ======================================================================


class TestWorkflowStepDef:
    """Tests for WorkflowStepDef model."""

    def test_create_minimal(self):
        step = WorkflowStepDef(name="code", capability_required="code.generate")
        assert step.name == "code"
        assert step.capability_required == "code.generate"
        assert step.description == ""
        assert step.agent_id is None
        assert step.dependencies == []
        assert step.timeout == 0.0
        assert step.retry_policy is None

    def test_create_full(self):
        rp = StepRetryPolicy(max_attempts=5)
        step = WorkflowStepDef(
            name="test",
            description="Run tests",
            capability_required="code.test",
            agent_id="coder-agent",
            dependencies=["code"],
            timeout=60.0,
            retry_policy=rp,
            metadata={"key": "val"},
        )
        assert step.agent_id == "coder-agent"
        assert step.dependencies == ["code"]
        assert step.timeout == 60.0
        assert step.retry_policy.max_attempts == 5
        assert step.metadata["key"] == "val"

    def test_to_dict(self):
        step = WorkflowStepDef(name="s", capability_required="cap", dependencies=["a"])
        data = step.to_dict()
        assert data["name"] == "s"
        assert data["capability_required"] == "cap"
        assert data["dependencies"] == ["a"]
        assert data["retry_policy"] is None

    def test_from_dict_roundtrip(self):
        rp = StepRetryPolicy(max_attempts=2, retry_on_errors=["e"])
        step = WorkflowStepDef(
            name="s", capability_required="cap",
            dependencies=["a"], retry_policy=rp, metadata={"k": "v"},
        )
        data = step.to_dict()
        restored = WorkflowStepDef.from_dict(data)
        assert restored.name == "s"
        assert restored.capability_required == "cap"
        assert restored.dependencies == ["a"]
        assert restored.retry_policy is not None
        assert restored.retry_policy.max_attempts == 2
        assert restored.retry_policy.retry_on_errors == ["e"]
        assert restored.metadata["k"] == "v"


# ======================================================================
# PipelineWorkflow tests
# ======================================================================


class TestPipelineWorkflow:
    """Tests for PipelineWorkflow model."""

    def test_create(self):
        wf = PipelineWorkflow(
            name="test-workflow",
            description="A test workflow",
            steps=[
                WorkflowStepDef(name="s1", capability_required="code.generate"),
                WorkflowStepDef(name="s2", capability_required="code.test"),
            ],
            metadata={"env": "test"},
        )
        assert wf.name == "test-workflow"
        assert wf.description == "A test workflow"
        assert len(wf.steps) == 2
        assert wf.status == "created"
        assert wf.id
        assert wf.created_at
        assert wf.metadata["env"] == "test"

    def test_default_values(self):
        wf = PipelineWorkflow(name="empty")
        assert wf.steps == []
        assert wf.description == ""
        assert wf.metadata == {}
        assert wf.timeout == 0.0
        assert wf.status == "created"

    def test_to_dict(self):
        wf = PipelineWorkflow(
            name="w",
            steps=[WorkflowStepDef(name="s1", capability_required="cap")],
        )
        data = wf.to_dict()
        assert data["name"] == "w"
        assert len(data["steps"]) == 1
        assert data["steps"][0]["capability_required"] == "cap"
        assert data["status"] == "created"
        assert data["id"] == wf.id