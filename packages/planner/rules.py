"""Rule engine for the planner.

Sprint 3.2 — Planner Engine Core.

A simple forward-chaining rule system.  Each rule has a condition (a
callable that inspects a :class:`PlanningContext`) and an action (a
callable that mutates the context, e.g. adding suggested agents).

Rules are evaluated in registration order; all matching rules fire
(unless ``stop_on_match`` is set).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .context import PlanningContext

# Type aliases
ConditionFn = Callable[[PlanningContext], bool]
ActionFn = Callable[[PlanningContext], None]


@dataclass
class PlannerRule:
    """A single planning rule.

    Attributes:
        name: Human-readable identifier.
        condition: Returns ``True`` when the rule should fire.
        action: Mutates the context when the rule fires.
        priority: Higher priority rules are evaluated first.
        stop_on_match: If ``True``, no further rules are evaluated after
            this one fires.
        enabled: Rules can be disabled without removal.
    """

    name: str
    condition: ConditionFn
    action: ActionFn
    priority: int = 0
    stop_on_match: bool = False
    enabled: bool = True


class PlannerRuleEngine:
    """Evaluate and apply planning rules against a :class:`PlanningContext`.

    Usage::

        engine = PlannerRuleEngine()
        engine.register_rule(PlannerRule(
            name="frontend_detection",
            condition=lambda ctx: "frontend" in ctx.goal.lower(),
            action=lambda ctx: ctx.suggested_agents.append("frontend_agent"),
        ))
        engine.apply(context)
    """

    def __init__(self) -> None:
        self._rules: list[PlannerRule] = []

    # -- rule management ------------------------------------------------------

    def register_rule(self, rule: PlannerRule) -> None:
        """Add a rule to the engine.

        Rules are stored in priority-descending order.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name.

        Returns:
            ``True`` if the rule was found and removed.
        """
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    def get_rule(self, name: str) -> PlannerRule | None:
        """Look up a rule by name. Returns ``None`` if not found."""
        for r in self._rules:
            if r.name == name:
                return r
        return None

    def enable_rule(self, name: str) -> None:
        """Enable a rule by name (no-op if not found)."""
        rule = self.get_rule(name)
        if rule is not None:
            rule.enabled = True

    def disable_rule(self, name: str) -> None:
        """Disable a rule by name (no-op if not found)."""
        rule = self.get_rule(name)
        if rule is not None:
            rule.enabled = False

    @property
    def rules(self) -> list[PlannerRule]:
        """Return a copy of the registered rules list."""
        return list(self._rules)

    @property
    def rule_count(self) -> int:
        """Number of registered rules."""
        return len(self._rules)

    # -- evaluation -----------------------------------------------------------

    def evaluate(self, context: PlanningContext) -> list[str]:
        """Evaluate all enabled rules against *context*.

        Returns the names of rules that fired.
        """
        fired: list[str] = []
        for rule in self._rules:
            if not rule.enabled:
                continue
            if rule.condition(context):
                rule.action(context)
                fired.append(rule.name)
                if rule.stop_on_match:
                    break
        return fired

    def apply(self, context: PlanningContext) -> list[str]:
        """Alias for :meth:`evaluate`."""
        return self.evaluate(context)


# ---------------------------------------------------------------------------
# Built-in default rules
# ---------------------------------------------------------------------------


def _default_rules() -> list[PlannerRule]:
    """Return the standard set of planner rules.

    These cover common agent-detection patterns and task-type hints.
    """
    rules: list[PlannerRule] = []

    # -- Agent suggestion rules -----------------------------------------------

    def _make_agent_rule(name: str, keyword: str, agent: str) -> PlannerRule:
        kw_lower = keyword.lower()

        def _condition(ctx: PlanningContext, k: str = kw_lower) -> bool:
            return k in ctx.goal.lower()

        def _action(ctx: PlanningContext, a: str = agent) -> None:
            if a not in ctx.suggested_agents:
                ctx.suggested_agents.append(a)

        return PlannerRule(
            name=name,
            condition=_condition,
            action=_action,
            priority=10,
        )

    rules.append(_make_agent_rule("detect_frontend", "frontend", "frontend_agent"))
    rules.append(_make_agent_rule("detect_backend", "backend", "backend_agent"))
    rules.append(_make_agent_rule("detect_database", "base de datos", "database_agent"))
    rules.append(_make_agent_rule("detect_database_en", "database", "database_agent"))
    rules.append(_make_agent_rule("detect_security", "security", "security_agent"))
    rules.append(_make_agent_rule("detect_security_es", "seguridad", "security_agent"))
    rules.append(_make_agent_rule("detect_auth", "auth", "security_agent"))
    rules.append(_make_agent_rule("detect_auth_es", "autenticación", "security_agent"))
    rules.append(_make_agent_rule("detect_deploy", "deploy", "devops_agent"))
    rules.append(_make_agent_rule("deploy_es", "desplegar", "devops_agent"))
    rules.append(_make_agent_rule("detect_git", "git", "git_agent"))
    rules.append(_make_agent_rule("detect_test", "test", "tester_agent"))
    rules.append(_make_agent_rule("detect_test_es", "probar", "tester_agent"))
    rules.append(_make_agent_rule("detect_docs", "document", "docs_agent"))
    rules.append(_make_agent_rule("detect_docs_es", "documentar", "docs_agent"))
    rules.append(_make_agent_rule("detect_review", "review", "reviewer_agent"))
    rules.append(_make_agent_rule("detect_research", "research", "research_agent"))
    rules.append(_make_agent_rule("detect_research_es", "investigar", "research_agent"))

    # -- Task-type hint rules -------------------------------------------------

    rules.append(
        PlannerRule(
            name="hint_bugfix",
            condition=lambda ctx: any(
                kw in ctx.goal.lower()
                for kw in ("fix", "bug", "error", "patch", "resolver", "corregir")
            ),
            action=lambda ctx: ctx.metadata.update({"hint_type": "bugfix"}),
            priority=5,
        )
    )

    rules.append(
        PlannerRule(
            name="hint_research",
            condition=lambda ctx: any(
                kw in ctx.goal.lower()
                for kw in ("research", "investigate", "explore", "investigar", "explorar")
            ),
            action=lambda ctx: ctx.metadata.update({"hint_type": "research"}),
            priority=5,
        )
    )

    rules.append(
        PlannerRule(
            name="hint_documentation",
            condition=lambda ctx: any(
                kw in ctx.goal.lower()
                for kw in ("document", "readme", "docs", "documentar", "documentación")
            ),
            action=lambda ctx: ctx.metadata.update({"hint_type": "documentation"}),
            priority=5,
        )
    )

    return rules


def create_default_rule_engine() -> PlannerRuleEngine:
    """Create a :class:`PlannerRuleEngine` pre-loaded with default rules."""
    engine = PlannerRuleEngine()
    for rule in _default_rules():
        engine.register_rule(rule)
    return engine
