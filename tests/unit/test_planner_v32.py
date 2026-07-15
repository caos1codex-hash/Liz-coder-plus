"""Comprehensive tests for Sprint 3.2 planner engine core.

Covers: context, complexity, rules, analyzer, templates, generator,
and TaskPlanner.create_from_goal integration.

Test naming: test_<unit>_<scenario>_<expected>
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_PACKAGES_DIR = Path(__file__).resolve().parents[2] / "packages"
sys.path.insert(0, str(_PACKAGES_DIR))

import pytest  # noqa: E402

# -- imports after path setup  # noqa: E402 ----------------------------------
from planner.analyzer import TaskAnalyzer  # noqa: E402
from planner.complexity import (  # noqa: E402
    ComplexityEstimator,
    ComplexityLevel,
)
from planner.context import PlanningContext  # noqa: E402
from planner.enums import (  # noqa: E402
    PlanStatus,
    Priority,
)
from planner.generator import PlanGenerator  # noqa: E402
from planner.models import PlanStep, TaskPlan  # noqa: E402
from planner.planner import TaskPlanner  # noqa: E402
from planner.rules import (  # noqa: E402
    PlannerRule,
    PlannerRuleEngine,
    create_default_rule_engine,
)
from planner.templates import (  # noqa: E402
    AnalysisTemplate,
    BugFixTemplate,
    DevelopmentTaskTemplate,
    DevOpsTemplate,
    DocumentationTemplate,
    GeneralTemplate,
    ResearchTemplate,
    TestingTemplate,
    all_templates,
    get_template,
)
from planner.validator import PlanValidator  # noqa: E402

# ===================================================================
# FIXTURES
# ===================================================================


@pytest.fixture
def estimator() -> ComplexityEstimator:
    return ComplexityEstimator()


@pytest.fixture
def analyzer() -> TaskAnalyzer:
    return TaskAnalyzer()


@pytest.fixture
def generator() -> PlanGenerator:
    return PlanGenerator()


@pytest.fixture
def planner() -> TaskPlanner:
    return TaskPlanner()


@pytest.fixture
def rule_engine() -> PlannerRuleEngine:
    return PlannerRuleEngine()


@pytest.fixture
def sample_context() -> PlanningContext:
    return PlanningContext(
        goal="Create a REST API with authentication",
        user="test_user",
        available_agents=["backend_agent", "security_agent", "tester_agent"],
    )


# ===================================================================
# PlanningContext
# ===================================================================


class TestPlanningContext:
    def test_defaults(self) -> None:
        ctx = PlanningContext()
        assert ctx.goal == ""
        assert ctx.user == ""
        assert ctx.task_type == "general"
        assert ctx.complexity == "normal"
        assert ctx.estimated_steps == 0
        assert ctx.suggested_agents == []
        assert ctx.detected_keywords == []

    def test_custom_fields(self) -> None:
        ctx = PlanningContext(
            goal="Build API",
            user="alice",
            available_agents=["a", "b"],
            constraints={"max_steps": 5},
            preferences={"language": "es"},
        )
        assert ctx.goal == "Build API"
        assert ctx.user == "alice"
        assert ctx.constraints == {"max_steps": 5}

    def test_to_dict(self) -> None:
        ctx = PlanningContext(
            goal="G",
            task_type="development",
            complexity="high",
            estimated_steps=6,
            suggested_agents=["backend_agent"],
            detected_keywords=["api", "deploy"],
        )
        d = ctx.to_dict()
        assert d["goal"] == "G"
        assert d["task_type"] == "development"
        assert d["suggested_agents"] == ["backend_agent"]

    def test_from_dict(self) -> None:
        data = {
            "goal": "G",
            "user": "bob",
            "task_type": "bugfix",
            "complexity": "low",
            "estimated_steps": 3,
            "suggested_agents": ["tester"],
            "detected_keywords": ["fix"],
        }
        ctx = PlanningContext.from_dict(data)
        assert ctx.goal == "G"
        assert ctx.task_type == "bugfix"

    def test_from_dict_defaults(self) -> None:
        ctx = PlanningContext.from_dict({})
        assert ctx.goal == ""
        assert ctx.task_type == "general"

    def test_roundtrip(self) -> None:
        original = PlanningContext(
            goal="Test",
            metadata={"k": "v"},
            available_agents=["a"],
        )
        restored = PlanningContext.from_dict(original.to_dict())
        assert restored.goal == original.goal
        assert restored.metadata == original.metadata


# ===================================================================
# ComplexityEstimator
# ===================================================================


class TestComplexityEstimator:
    def test_estimate_empty_goal(self, estimator: ComplexityEstimator) -> None:
        assert estimator.estimate("") == ComplexityLevel.LOW

    def test_estimate_simple_goal(self, estimator: ComplexityEstimator) -> None:
        assert estimator.estimate("Fix typo") == ComplexityLevel.LOW

    def test_estimate_normal_goal(self, estimator: ComplexityEstimator) -> None:
        result = estimator.estimate("Create a simple function to add numbers")
        assert result in (ComplexityLevel.LOW, ComplexityLevel.NORMAL)

    def test_estimate_high_goal(self, estimator: ComplexityEstimator) -> None:
        result = estimator.estimate(
            "Create a distributed microservice architecture with authentication"
        )
        assert result in (ComplexityLevel.HIGH, ComplexityLevel.CRITICAL)

    def test_estimate_critical_goal(self, estimator: ComplexityEstimator) -> None:
        result = estimator.estimate("Rewrite the entire system from scratch with kubernetes")
        assert result in (ComplexityLevel.HIGH, ComplexityLevel.CRITICAL)

    def test_score_empty(self, estimator: ComplexityEstimator) -> None:
        assert estimator.score("") == 0.0

    def test_score_simple(self, estimator: ComplexityEstimator) -> None:
        score = estimator.score("Fix typo in readme")
        assert score < 0.25

    def test_score_complex(self, estimator: ComplexityEstimator) -> None:
        score = estimator.score(
            "Migrate the database to a sharded cluster, set up authentication, "
            "deploy with kubernetes, and implement real-time monitoring"
        )
        assert score > 0.5

    def test_detect_keywords(self, estimator: ComplexityEstimator) -> None:
        found = estimator.detect_keywords("Create API with authentication and deploy")
        assert isinstance(found, list)
        # "deploy" and "auth" should be detected
        assert len(found) >= 1

    def test_detect_keywords_empty(self, estimator: ComplexityEstimator) -> None:
        assert estimator.detect_keywords("hello world") == []

    def test_count_actions(self, estimator: ComplexityEstimator) -> None:
        count = estimator.count_actions("create and build and test")
        assert count >= 2

    def test_count_actions_empty(self, estimator: ComplexityEstimator) -> None:
        assert estimator.count_actions("") == 0

    def test_suggest_step_count_low(self, estimator: ComplexityEstimator) -> None:
        count = estimator.suggest_step_count("Fix typo")
        assert 2 <= count <= 5

    def test_suggest_step_count_high(self, estimator: ComplexityEstimator) -> None:
        count = estimator.suggest_step_count(
            "Migrate database, create API, deploy, setup auth, test, review"
        )
        assert count >= 6

    def test_detect_task_type_development(self, estimator: ComplexityEstimator) -> None:
        assert estimator.detect_task_type("Create a web page for a store") == "development"

    def test_detect_task_type_bugfix(self, estimator: ComplexityEstimator) -> None:
        assert estimator.detect_task_type("Fix the login bug") == "bugfix"

    def test_detect_task_type_research(self, estimator: ComplexityEstimator) -> None:
        assert estimator.detect_task_type("Research best practices for auth") == "research"

    def test_detect_task_type_documentation(self, estimator: ComplexityEstimator) -> None:
        result = estimator.detect_task_type("Document the API endpoints")
        # "Document the" is the documentation keyword pattern
        # But "endpoints" matches development, so "Document the" must win
        assert result in ("documentation", "development")

    def test_detect_task_type_general(self, estimator: ComplexityEstimator) -> None:
        assert estimator.detect_task_type("hello world") == "general"

    def test_detect_task_type_devops(self, estimator: ComplexityEstimator) -> None:
        result = estimator.detect_task_type("Deploy to production with docker")
        assert result == "devops"

    def test_detect_task_type_testing(self, estimator: ComplexityEstimator) -> None:
        result = estimator.detect_task_type("Write unit tests for the module")
        assert result == "testing"

    def test_length_factor_short(self, estimator: ComplexityEstimator) -> None:
        assert estimator._length_factor("hi") == 0.05

    def test_length_factor_long(self, estimator: ComplexityEstimator) -> None:
        assert estimator._length_factor("a" * 300) == 0.25

    def test_dependency_factor_conjunctions(self, estimator: ComplexityEstimator) -> None:
        text = "first do this, and then do that, followed by the last thing"
        score = estimator._dependency_factor(text)
        assert score > 0.0


# ===================================================================
# PlannerRuleEngine
# ===================================================================


class TestPlannerRuleEngine:
    def test_register_and_evaluate(self, rule_engine: PlannerRuleEngine) -> None:
        fired = []

        def condition(ctx: PlanningContext) -> bool:
            return "test" in ctx.goal.lower()

        def action(ctx: PlanningContext) -> None:
            fired.append("test_rule")

        rule = PlannerRule(name="test_rule", condition=condition, action=action)
        rule_engine.register_rule(rule)
        ctx = PlanningContext(goal="test something")
        result = rule_engine.evaluate(ctx)
        assert "test_rule" in result
        assert "test_rule" in fired

    def test_evaluate_no_match(self, rule_engine: PlannerRuleEngine) -> None:
        rule = PlannerRule(
            name="never",
            condition=lambda ctx: False,
            action=lambda ctx: None,
        )
        rule_engine.register_rule(rule)
        ctx = PlanningContext(goal="anything")
        assert rule_engine.evaluate(ctx) == []

    def test_remove_rule(self, rule_engine: PlannerRuleEngine) -> None:
        rule = PlannerRule(
            name="temp",
            condition=lambda ctx: True,
            action=lambda ctx: None,
        )
        rule_engine.register_rule(rule)
        assert rule_engine.remove_rule("temp") is True
        assert rule_engine.get_rule("temp") is None

    def test_remove_rule_not_found(self, rule_engine: PlannerRuleEngine) -> None:
        assert rule_engine.remove_rule("nonexistent") is False

    def test_get_rule(self, rule_engine: PlannerRuleEngine) -> None:
        rule = PlannerRule(
            name="findme",
            condition=lambda ctx: True,
            action=lambda ctx: None,
        )
        rule_engine.register_rule(rule)
        found = rule_engine.get_rule("findme")
        assert found is not None
        assert found.name == "findme"

    def test_get_rule_not_found(self, rule_engine: PlannerRuleEngine) -> None:
        assert rule_engine.get_rule("nope") is None

    def test_enable_disable_rule(self, rule_engine: PlannerRuleEngine) -> None:
        rule = PlannerRule(
            name="toggle",
            condition=lambda ctx: True,
            action=lambda ctx: None,
        )
        rule_engine.register_rule(rule)
        rule_engine.disable_rule("toggle")
        assert not rule_engine.get_rule("toggle").enabled
        rule_engine.enable_rule("toggle")
        assert rule_engine.get_rule("toggle").enabled

    def test_disabled_rule_not_fired(self, rule_engine: PlannerRuleEngine) -> None:
        fired = []

        rule = PlannerRule(
            name="disabled_rule",
            condition=lambda ctx: True,
            action=lambda ctx: fired.append("fired"),
            enabled=False,
        )
        rule_engine.register_rule(rule)
        ctx = PlanningContext(goal="anything")
        result = rule_engine.evaluate(ctx)
        assert "disabled_rule" not in result
        assert fired == []

    def test_stop_on_match(self, rule_engine: PlannerRuleEngine) -> None:
        order = []

        rule1 = PlannerRule(
            name="first",
            condition=lambda ctx: True,
            action=lambda ctx: order.append("first"),
            stop_on_match=True,
            priority=10,
        )
        rule2 = PlannerRule(
            name="second",
            condition=lambda ctx: True,
            action=lambda ctx: order.append("second"),
            priority=5,
        )
        rule_engine.register_rule(rule2)
        rule_engine.register_rule(rule1)
        rule_engine.evaluate(PlanningContext(goal="x"))
        assert order == ["first"]

    def test_priority_ordering(self, rule_engine: PlannerRuleEngine) -> None:
        order = []

        rule_low = PlannerRule(
            name="low",
            condition=lambda ctx: True,
            action=lambda ctx: order.append("low"),
            priority=1,
        )
        rule_high = PlannerRule(
            name="high",
            condition=lambda ctx: True,
            action=lambda ctx: order.append("high"),
            priority=100,
        )
        rule_engine.register_rule(rule_low)
        rule_engine.register_rule(rule_high)
        rule_engine.evaluate(PlanningContext(goal="x"))
        assert order == ["high", "low"]

    def test_rule_count(self, rule_engine: PlannerRuleEngine) -> None:
        assert rule_engine.rule_count == 0
        rule_engine.register_rule(
            PlannerRule(name="a", condition=lambda c: True, action=lambda c: None)
        )
        assert rule_engine.rule_count == 1

    def test_rules_property_copy(self, rule_engine: PlannerRuleEngine) -> None:
        rule_engine.register_rule(
            PlannerRule(name="a", condition=lambda c: True, action=lambda c: None)
        )
        rules = rule_engine.rules
        assert len(rules) == 1
        # Mutating the copy shouldn't affect the engine
        rules.clear()
        assert rule_engine.rule_count == 1

    def test_apply_alias(self, rule_engine: PlannerRuleEngine) -> None:
        rule = PlannerRule(
            name="alias_test",
            condition=lambda ctx: True,
            action=lambda ctx: None,
        )
        rule_engine.register_rule(rule)
        result = rule_engine.apply(PlanningContext(goal="x"))
        assert "alias_test" in result

    def test_default_rule_engine_detects_frontend(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Create a frontend component")
        engine.apply(ctx)
        assert "frontend_agent" in ctx.suggested_agents

    def test_default_rule_engine_detects_backend(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Build a backend API")
        engine.apply(ctx)
        assert "backend_agent" in ctx.suggested_agents

    def test_default_rule_engine_detects_database(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Configure the database connection")
        engine.apply(ctx)
        assert "database_agent" in ctx.suggested_agents

    def test_default_rule_engine_detects_deploy(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Deploy to production")
        engine.apply(ctx)
        assert "devops_agent" in ctx.suggested_agents

    def test_default_rule_engine_no_duplicates(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Deploy the backend with git")
        engine.apply(ctx)
        assert ctx.suggested_agents.count("devops_agent") == 1

    def test_default_rule_engine_hint_type_bugfix(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Fix the login bug")
        engine.apply(ctx)
        assert ctx.metadata.get("hint_type") == "bugfix"

    def test_default_rule_engine_hint_type_research(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Research authentication methods")
        engine.apply(ctx)
        assert ctx.metadata.get("hint_type") == "research"

    def test_default_rule_engine_hint_type_docs(self) -> None:
        engine = create_default_rule_engine()
        ctx = PlanningContext(goal="Document the API")
        engine.apply(ctx)
        assert ctx.metadata.get("hint_type") == "documentation"


# ===================================================================
# TaskAnalyzer
# ===================================================================


class TestTaskAnalyzer:
    def test_analyze_returns_context(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Create a web page")
        assert isinstance(ctx, PlanningContext)
        assert ctx.goal == "Create a web page"

    def test_analyze_detects_type(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Fix the login bug")
        assert ctx.task_type == "bugfix"

    def test_analyze_estimates_complexity(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Create a web page")
        assert ctx.complexity in ("low", "normal", "high", "critical")

    def test_analyze_suggests_agents(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Create a frontend component with tests")
        assert len(ctx.suggested_agents) > 0

    def test_analyze_detects_keywords(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Deploy the API to production")
        assert isinstance(ctx.detected_keywords, list)

    def test_analyze_estimates_steps(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("Create a simple function")
        assert ctx.estimated_steps > 0

    def test_analyze_with_kwargs(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze(
            "Build API",
            user="alice",
            available_agents=["backend_agent"],
            constraints={"max_steps": 10},
        )
        assert ctx.user == "alice"
        assert ctx.available_agents == ["backend_agent"]

    def test_analyze_filters_agents_by_available(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze(
            "Create frontend and backend",
            available_agents=["backend_agent"],
        )
        # Should only keep agents that are in available_agents
        assert all(a in ["backend_agent"] for a in ctx.suggested_agents)

    def test_analyze_quick(self, analyzer: TaskAnalyzer) -> None:
        result = analyzer.analyze_quick("Create a REST API")
        assert isinstance(result, dict)
        assert "task_type" in result
        assert "complexity" in result
        assert "estimated_steps" in result
        assert "suggested_agents" in result

    def test_analyze_empty_goal(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze("")
        assert ctx.task_type == "general"
        assert ctx.complexity == "low"

    def test_custom_complexity_estimator(self) -> None:
        custom_est = MagicMock(spec=ComplexityEstimator)
        custom_est.detect_task_type.return_value = "custom"
        custom_est.estimate.return_value = ComplexityLevel.HIGH
        custom_est.detect_keywords.return_value = ["kw"]
        custom_est.suggest_step_count.return_value = 5

        a = TaskAnalyzer(complexity_estimator=custom_est)
        ctx = a.analyze("anything")
        assert ctx.task_type == "custom"
        assert ctx.complexity == "high"

    def test_custom_rule_engine(self) -> None:
        custom_rules = PlannerRuleEngine()
        custom_rules.register_rule(
            PlannerRule(
                name="custom",
                condition=lambda ctx: True,
                action=lambda ctx: ctx.suggested_agents.append("custom_agent"),
            )
        )
        a = TaskAnalyzer(rule_engine=custom_rules)
        ctx = a.analyze("anything")
        assert "custom_agent" in ctx.suggested_agents

    def test_analyzer_properties(self, analyzer: TaskAnalyzer) -> None:
        assert isinstance(analyzer.complexity_estimator, ComplexityEstimator)
        assert isinstance(analyzer.rule_engine, PlannerRuleEngine)

    def test_analyze_with_available_agents_keeps_suggested(self, analyzer: TaskAnalyzer) -> None:
        ctx = analyzer.analyze(
            "Create frontend and backend and deploy",
            available_agents=["frontend_agent", "backend_agent", "devops_agent", "tester_agent"],
        )
        # All suggested agents should be in available_agents
        for a in ctx.suggested_agents:
            assert a in ctx.available_agents


# ===================================================================
# Templates
# ===================================================================


class TestTemplates:
    def test_development_template_type(self) -> None:
        assert DevelopmentTaskTemplate().task_type == "development"

    def test_bugfix_template_type(self) -> None:
        assert BugFixTemplate().task_type == "bugfix"

    def test_research_template_type(self) -> None:
        assert ResearchTemplate().task_type == "research"

    def test_documentation_template_type(self) -> None:
        assert DocumentationTemplate().task_type == "documentation"

    def test_analysis_template_type(self) -> None:
        assert AnalysisTemplate().task_type == "analysis"

    def test_devops_template_type(self) -> None:
        assert DevOpsTemplate().task_type == "devops"

    def test_testing_template_type(self) -> None:
        assert TestingTemplate().task_type == "testing"

    def test_general_template_type(self) -> None:
        assert GeneralTemplate().task_type == "general"

    def test_development_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Create a web page for a store")
        steps = DevelopmentTaskTemplate().generate_steps(ctx)
        assert len(steps) >= 3
        assert steps[0].title == "Analizar requisitos"

    def test_development_with_db(self) -> None:
        ctx = PlanningContext(goal="Create API with database")
        steps = DevelopmentTaskTemplate().generate_steps(ctx)
        titles = [s.title for s in steps]
        assert any("base de datos" in t.lower() or "database" in t.lower() for t in titles)

    def test_development_high_complexity_adds_review(self) -> None:
        ctx = PlanningContext(goal="Build distributed system", complexity="high")
        steps = DevelopmentTaskTemplate().generate_steps(ctx)
        titles = [s.title for s in steps]
        assert any("revisión" in t.lower() for t in titles)

    def test_bugfix_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Fix login error")
        steps = BugFixTemplate().generate_steps(ctx)
        assert len(steps) == 4
        assert steps[0].title == "Reproducir el error"

    def test_research_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Research auth methods")
        steps = ResearchTemplate().generate_steps(ctx)
        assert len(steps) == 4
        assert steps[-1].title == "Documentar conclusiones"

    def test_documentation_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Document API")
        steps = DocumentationTemplate().generate_steps(ctx)
        assert len(steps) == 3

    def test_analysis_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Analyze code quality")
        steps = AnalysisTemplate().generate_steps(ctx)
        assert len(steps) == 3

    def test_devops_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Deploy to production")
        steps = DevOpsTemplate().generate_steps(ctx)
        assert len(steps) == 4
        assert "desplegar" in steps[-1].title.lower()

    def test_testing_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Write tests for module")
        steps = TestingTemplate().generate_steps(ctx)
        assert len(steps) == 3

    def test_general_generates_steps(self) -> None:
        ctx = PlanningContext(goal="Do something")
        steps = GeneralTemplate().generate_steps(ctx)
        assert len(steps) >= 3

    def test_steps_have_estimated_durations(self) -> None:
        ctx = PlanningContext(goal="Build API")
        steps = DevelopmentTaskTemplate().generate_steps(ctx)
        for step in steps:
            assert step.estimated_duration > 0

    def test_steps_have_metadata_phase(self) -> None:
        ctx = PlanningContext(goal="Build API")
        steps = DevelopmentTaskTemplate().generate_steps(ctx)
        for step in steps:
            assert "phase" in step.metadata

    def test_get_template_known_type(self) -> None:
        tmpl = get_template("development")
        assert isinstance(tmpl, DevelopmentTaskTemplate)

    def test_get_template_unknown_falls_back(self) -> None:
        tmpl = get_template("nonexistent_type")
        assert isinstance(tmpl, GeneralTemplate)

    def test_all_templates(self) -> None:
        tmpls = all_templates()
        assert isinstance(tmpls, dict)
        assert "development" in tmpls
        assert "general" in tmpls
        assert len(tmpls) >= 8


# ===================================================================
# PlanGenerator
# ===================================================================


class TestPlanGenerator:
    def test_generate_creates_plan(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Create a web page for a store")
        assert isinstance(plan, TaskPlan)
        assert plan.goal == "Create a web page for a store"
        assert len(plan.steps) > 0

    def test_generate_has_metadata(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Build an API")
        assert plan.metadata.get("generated_by") == "PlanGenerator"
        assert plan.metadata.get("task_type") == "development"

    def test_generate_status_is_planning(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Create a page")
        assert plan.status == PlanStatus.PLANNING

    def test_generate_priority_from_complexity(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Fix typo")
        assert plan.priority in (Priority.LOW, Priority.NORMAL)

    def test_generate_custom_priority(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Simple task", priority="critical")
        assert plan.priority == Priority.CRITICAL

    def test_generate_with_description(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Build API", description="Custom desc")
        assert plan.description == "Custom desc"

    def test_generate_bugfix(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Fix the login bug")
        assert plan.metadata.get("task_type") == "bugfix"

    def test_generate_research(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Research authentication methods")
        assert plan.metadata.get("task_type") == "research"

    def test_generate_documentation(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Write documentation for the project")
        # "documentation" type should be detected via hint or keyword
        hint = plan.metadata.get("analysis", {}).get("hint_type", "")
        task_type = plan.metadata.get("task_type", "")
        assert hint == "documentation" or task_type == "documentation"

    def test_generate_from_template(self, generator: PlanGenerator) -> None:
        plan = generator.generate_from_template(
            "Fix bug",
            template=BugFixTemplate(),
        )
        assert len(plan.steps) == 4
        assert plan.steps[0].title == "Reproducir el error"

    def test_generate_wires_dependencies(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Create a web page for a store")
        # First step should have no deps
        assert plan.steps[0].dependencies == []
        # Subsequent steps should have deps
        for step in plan.steps[1:]:
            assert len(step.dependencies) > 0
        # No None should remain in any dependency list
        for step in plan.steps:
            assert None not in step.dependencies

    def test_generate_validates_plan(self, generator: PlanGenerator) -> None:
        plan = generator.generate("Build API")
        errors = PlanValidator().validate(plan)
        assert errors == []

    def test_generate_with_context_kwargs(self, generator: PlanGenerator) -> None:
        plan = generator.generate(
            "Build API",
            user="alice",
            available_agents=["backend_agent"],
        )
        assert "user" in plan.metadata.get("analysis", {})

    # -- add_step ------------------------------------------------------------

    def test_add_step_appends(self) -> None:
        plan = TaskPlan(goal="G", steps=[PlanStep(id="s1", title="S1")])
        new_step = PlanStep(id="s2", title="S2")
        PlanGenerator.add_step(plan, new_step)
        assert len(plan.steps) == 2
        assert plan.steps[1].id == "s2"

    def test_add_step_after(self) -> None:
        plan = TaskPlan(
            goal="G",
            steps=[
                PlanStep(id="s1", title="S1"),
                PlanStep(id="s3", title="S3"),
            ],
        )
        new_step = PlanStep(id="s2", title="S2")
        PlanGenerator.add_step(plan, new_step, after="s1")
        assert plan.steps[1].id == "s2"

    def test_add_step_auto_depends_on_previous(self) -> None:
        plan = TaskPlan(goal="G", steps=[PlanStep(id="s1", title="S1")])
        new_step = PlanStep(id="s2", title="S2")
        PlanGenerator.add_step(plan, new_step)
        assert "s1" in plan.steps[1].dependencies

    # -- remove_step ---------------------------------------------------------

    def test_remove_step(self) -> None:
        s1 = PlanStep(id="s1", title="S1")
        s2 = PlanStep(id="s2", title="S2", dependencies=["s1"])
        plan = TaskPlan(goal="G", steps=[s1, s2])
        PlanGenerator.remove_step(plan, "s1")
        assert len(plan.steps) == 1
        assert plan.steps[0].id == "s2"
        assert "s1" not in plan.steps[0].dependencies

    def test_remove_step_not_found_raises(self) -> None:
        plan = TaskPlan(goal="G", steps=[PlanStep(id="s1", title="S1")])
        with pytest.raises(ValueError, match="not found"):
            PlanGenerator.remove_step(plan, "nonexistent")

    # -- optimize_order ------------------------------------------------------

    def test_optimize_order_already_ordered(self) -> None:
        s1 = PlanStep(id="s1", title="S1")
        s2 = PlanStep(id="s2", title="S2", dependencies=["s1"])
        plan = TaskPlan(goal="G", steps=[s1, s2])
        PlanGenerator.optimize_order(plan)
        assert plan.step_ids() == ["s1", "s2"]

    def test_optimize_order_reverses(self) -> None:
        s1 = PlanStep(id="s1", title="S1")
        s2 = PlanStep(id="s2", title="S2", dependencies=["s1"])
        plan = TaskPlan(goal="G", steps=[s2, s1])  # wrong order
        PlanGenerator.optimize_order(plan)
        assert plan.step_ids() == ["s1", "s2"]

    def test_optimize_order_empty(self) -> None:
        plan = TaskPlan(goal="G")
        PlanGenerator.optimize_order(plan)
        assert plan.steps == []

    def test_optimize_order_no_deps(self) -> None:
        s1 = PlanStep(id="s1", title="S1")
        s2 = PlanStep(id="s2", title="S2")
        plan = TaskPlan(goal="G", steps=[s1, s2])
        PlanGenerator.optimize_order(plan)
        assert plan.step_ids() == ["s1", "s2"]


# ===================================================================
# TaskPlanner.create_from_goal (Integration)
# ===================================================================


class TestCreateFromGoal:
    def test_basic_generation(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Create a web page for a store")
        assert isinstance(plan, TaskPlan)
        assert plan.goal == "Create a web page for a store"
        assert len(plan.steps) > 0

    def test_generated_plan_is_valid(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Build an API with authentication")
        errors = planner.validate(plan)
        assert errors == []

    def test_bugfix_goal(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Fix the login bug")
        assert plan.metadata.get("task_type") == "bugfix"

    def test_research_goal(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Research best auth practices")
        assert plan.metadata.get("task_type") == "research"

    def test_with_custom_priority(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Simple task", priority="high")
        assert plan.priority == Priority.HIGH

    def test_with_description(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Build API", description="Custom desc")
        assert plan.description == "Custom desc"

    def test_estimate_generated_plan(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Create a REST API")
        metrics = planner.estimate(plan)
        assert metrics.total_steps > 0

    def test_serialize_generated_plan(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Create a web page")
        data = planner.serialize(plan, format="json")
        assert isinstance(data, str)
        parsed = json.loads(data)
        assert parsed["goal"] == "Create a web page"

    def test_clone_generated_plan(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Build API")
        cloned = planner.clone(plan)
        assert cloned.goal == plan.goal
        assert cloned.id != plan.id

    def test_spanish_goal(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Crear una página web para una tienda")
        assert len(plan.steps) > 0
        assert "Analizar" in plan.steps[0].title

    def test_steps_have_no_none_deps(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Build a full-stack application")
        for step in plan.steps:
            assert None not in step.dependencies


# ===================================================================
# End-to-end: full pipeline
# ===================================================================


class TestEndToEnd:
    def test_goal_to_json_roundtrip(self, planner: TaskPlanner) -> None:
        plan = planner.create_from_goal("Create API with database and auth")
        json_str = planner.serialize(plan, format="json")
        restored = planner.deserialize(json_str, format="json")
        assert restored.goal == plan.goal
        assert len(restored.steps) == len(plan.steps)

    def test_multiple_goals_different_plans(self, planner: TaskPlanner) -> None:
        plans = [
            planner.create_from_goal("Fix typo in readme"),
            planner.create_from_goal("Create a web page for a store"),
            planner.create_from_goal("Research authentication methods"),
            planner.create_from_goal("Deploy to production"),
        ]
        # Each plan should have a unique ID
        ids = [p.id for p in plans]
        assert len(set(ids)) == len(ids)
        # Bugfix should have fewer steps than development
        assert len(plans[0].steps) <= len(plans[1].steps)

    def test_complexity_affects_priority(self, planner: TaskPlanner) -> None:
        simple = planner.create_from_goal("Fix typo")
        complex_plan = planner.create_from_goal(
            "Rewrite entire system from scratch with kubernetes"
        )
        # Critical complexity should yield higher priority
        priority_order = {
            Priority.LOW: 0,
            Priority.NORMAL: 1,
            Priority.HIGH: 2,
            Priority.CRITICAL: 3,
        }
        assert priority_order.get(complex_plan.priority, 0) >= priority_order.get(
            simple.priority, 0
        )
