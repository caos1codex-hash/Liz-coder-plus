"""Plan templates.

Sprint 3.2 — Planner Engine Core.

Pre-defined step sequences for common task types.  Each template
knows how to produce an ordered list of :class:`PlanStep` instances
given a :class:`PlanningContext`.

Templates are selected by :class:`PlanGenerator` based on the
``task_type`` field in the context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .context import PlanningContext
from .models import PlanStep


class PlanTemplate(ABC):
    """Base class for plan templates.

    Each template produces a sequence of :class:`PlanStep` instances
    tailored to a specific task type.
    """

    @property
    @abstractmethod
    def task_type(self) -> str:
        """The task type this template handles."""

    @abstractmethod
    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        """Produce an ordered list of steps for *context*."""


# ---------------------------------------------------------------------------
# Concrete templates
# ---------------------------------------------------------------------------


class DevelopmentTaskTemplate(PlanTemplate):
    """Template for software development tasks."""

    @property
    def task_type(self) -> str:
        return "development"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        steps: list[PlanStep] = []
        goal = context.goal.lower()

        # Step 1: Requirements analysis
        steps.append(
            PlanStep(
                title="Analizar requisitos",
                description=f"Analizar los requisitos para: {context.goal}",
                estimated_duration=300.0,
                required_agents=self._pick_agents(context, ["research_agent"]),
                metadata={"phase": "analysis"},
            )
        )

        # Step 2: Design / architecture
        has_frontend = any(kw in goal for kw in ("frontend", "página", "web", "ui", "interfaz"))
        has_backend = any(kw in goal for kw in ("backend", "api", "servidor", "endpoint"))
        has_db = any(kw in goal for kw in ("database", "base de datos", "db", "sql"))

        design_title = "Diseñar arquitectura"
        if has_frontend and has_backend:
            design_title = "Diseñar arquitectura full-stack"
        elif has_frontend:
            design_title = "Diseñar interfaz de usuario"
        elif has_backend:
            design_title = "Diseñar arquitectura del servidor"

        steps.append(
            PlanStep(
                title=design_title,
                description=f"Diseñar la arquitectura para: {context.goal}",
                estimated_duration=600.0,
                dependencies=[steps[0].id],
                required_agents=self._pick_agents(context, ["backend_agent", "frontend_agent"]),
                metadata={"phase": "design"},
            )
        )

        # Step 3: Implementation
        impl_title = "Implementar solución"
        if has_frontend:
            impl_title = "Implementar interfaz"
        if has_backend:
            impl_title = (
                "Implementar lógica del servidor"
                if not has_frontend
                else "Implementar frontend y backend"
            )

        steps.append(
            PlanStep(
                title=impl_title,
                description=f"Implementar la solución principal para: {context.goal}",
                estimated_duration=1800.0,
                dependencies=[steps[1].id],
                required_agents=self._pick_agents(
                    context, context.suggested_agents or ["backend_agent", "frontend_agent"]
                ),
                metadata={"phase": "implementation"},
            )
        )

        # Step 4: Database (if detected)
        if has_db:
            steps.append(
                PlanStep(
                    title="Configurar base de datos",
                    description="Diseñar e implementar el esquema de base de datos",
                    estimated_duration=600.0,
                    dependencies=[steps[1].id],
                    required_agents=self._pick_agents(context, ["database_agent"]),
                    metadata={"phase": "database"},
                )
            )
            # Update implementation to depend on DB step too
            steps[2].dependencies.append(steps[-1].id)

        # Step 5: Testing
        steps.append(
            PlanStep(
                title="Probar funcionamiento",
                description=f"Ejecutar pruebas para verificar: {context.goal}",
                estimated_duration=600.0,
                dependencies=[steps[2].id],
                required_agents=self._pick_agents(context, ["tester_agent"]),
                metadata={"phase": "testing"},
            )
        )

        # Step 6: Review (for high/critical complexity)
        if context.complexity in ("high", "critical"):
            steps.append(
                PlanStep(
                    title="Revisión de código",
                    description="Revisar la implementación antes de finalizar",
                    estimated_duration=300.0,
                    dependencies=[steps[-1].id],
                    required_agents=self._pick_agents(context, ["reviewer_agent"]),
                    metadata={"phase": "review"},
                )
            )

        return steps

    @staticmethod
    def _pick_agents(
        context: PlanningContext,
        candidates: list[str],
    ) -> list[str]:
        """Return the intersection of candidates and available/suggested agents."""
        if context.suggested_agents:
            return [a for a in candidates if a in context.suggested_agents]
        if context.available_agents:
            return [a for a in candidates if a in context.available_agents]
        return candidates


class BugFixTemplate(PlanTemplate):
    """Template for bug fix tasks."""

    @property
    def task_type(self) -> str:
        return "bugfix"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Reproducir el error",
                description=f"Reproducir y documentar el error reportado en: {context.goal}",
                estimated_duration=300.0,
                required_agents=["research_agent"],
                metadata={"phase": "investigation"},
            ),
            PlanStep(
                title="Identificar causa raíz",
                description="Analizar el código para encontrar la causa del error",
                estimated_duration=600.0,
                dependencies=[None],  # will be set after creation
                required_agents=["backend_agent"],
                metadata={"phase": "analysis"},
            ),
            PlanStep(
                title="Implementar corrección",
                description="Aplicar la corrección al código fuente",
                estimated_duration=600.0,
                required_agents=["backend_agent"],
                metadata={"phase": "fix"},
            ),
            PlanStep(
                title="Verificar corrección",
                description="Confirmar que el error está resuelto y no hay regresiones",
                estimated_duration=300.0,
                required_agents=["tester_agent"],
                metadata={"phase": "verification"},
            ),
        ]


class ResearchTemplate(PlanTemplate):
    """Template for research / investigation tasks."""

    @property
    def task_type(self) -> str:
        return "research"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Definir alcance de investigación",
                description=f"Delimitar el alcance para: {context.goal}",
                estimated_duration=300.0,
                required_agents=["research_agent"],
                metadata={"phase": "scoping"},
            ),
            PlanStep(
                title="Recopilar información",
                description="Buscar y recopilar fuentes relevantes",
                estimated_duration=900.0,
                dependencies=[None],
                required_agents=["research_agent"],
                metadata={"phase": "collection"},
            ),
            PlanStep(
                title="Analizar hallazgos",
                description="Analizar y sintetizar la información recopilada",
                estimated_duration=600.0,
                dependencies=[None],
                required_agents=["research_agent"],
                metadata={"phase": "analysis"},
            ),
            PlanStep(
                title="Documentar conclusiones",
                description="Preparar documentación con los resultados",
                estimated_duration=300.0,
                required_agents=["docs_agent"],
                metadata={"phase": "documentation"},
            ),
        ]


class DocumentationTemplate(PlanTemplate):
    """Template for documentation tasks."""

    @property
    def task_type(self) -> str:
        return "documentation"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Revisar código o funcionalidad",
                description="Inspeccionar el código o característica a documentar",
                estimated_duration=300.0,
                required_agents=["research_agent"],
                metadata={"phase": "review"},
            ),
            PlanStep(
                title="Redactar documentación",
                description=f"Escribir documentación para: {context.goal}",
                estimated_duration=600.0,
                dependencies=[None],
                required_agents=["docs_agent"],
                metadata={"phase": "writing"},
            ),
            PlanStep(
                title="Revisar y publicar",
                description="Revisar la documentación y confirmar su publicación",
                estimated_duration=300.0,
                dependencies=[None],
                required_agents=["reviewer_agent"],
                metadata={"phase": "publish"},
            ),
        ]


class AnalysisTemplate(PlanTemplate):
    """Template for generic analysis tasks (code analysis, data analysis, etc.)."""

    @property
    def task_type(self) -> str:
        return "analysis"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Recopilar datos o código",
                description=f"Obtener los datos o código fuente para analizar: {context.goal}",
                estimated_duration=300.0,
                required_agents=["research_agent"],
                metadata={"phase": "collection"},
            ),
            PlanStep(
                title="Realizar análisis",
                description="Ejecutar el análisis detallado",
                estimated_duration=900.0,
                dependencies=[None],
                required_agents=["backend_agent"],
                metadata={"phase": "analysis"},
            ),
            PlanStep(
                title="Preparar informe",
                description="Documentar los resultados del análisis",
                estimated_duration=600.0,
                dependencies=[None],
                required_agents=["docs_agent"],
                metadata={"phase": "report"},
            ),
        ]


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------


# DevOps and Testing templates for completeness
class DevOpsTemplate(PlanTemplate):
    """Template for DevOps / deployment tasks."""

    @property
    def task_type(self) -> str:
        return "devops"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Revisar configuración actual",
                description="Inspeccionar la configuración de despliegue existente",
                estimated_duration=300.0,
                required_agents=["devops_agent"],
                metadata={"phase": "review"},
            ),
            PlanStep(
                title="Configurar pipeline",
                description="Crear o actualizar el pipeline de CI/CD",
                estimated_duration=600.0,
                dependencies=[None],
                required_agents=["devops_agent"],
                metadata={"phase": "configuration"},
            ),
            PlanStep(
                title="Probar despliegue",
                description="Ejecutar despliegue de prueba",
                estimated_duration=600.0,
                dependencies=[None],
                required_agents=["devops_agent", "tester_agent"],
                metadata={"phase": "testing"},
            ),
            PlanStep(
                title="Desplegar en producción",
                description="Ejecutar despliegue en el entorno de producción",
                estimated_duration=300.0,
                dependencies=[None],
                required_agents=["devops_agent"],
                metadata={"phase": "deploy"},
            ),
        ]


class TestingTemplate(PlanTemplate):
    """Template for testing-focused tasks."""

    @property
    def task_type(self) -> str:
        return "testing"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Analizar cobertura actual",
                description="Revisar las pruebas existentes y la cobertura",
                estimated_duration=300.0,
                required_agents=["tester_agent"],
                metadata={"phase": "analysis"},
            ),
            PlanStep(
                title="Escribir pruebas",
                description=f"Escribir pruebas para: {context.goal}",
                estimated_duration=900.0,
                dependencies=[None],
                required_agents=["tester_agent", "backend_agent"],
                metadata={"phase": "writing"},
            ),
            PlanStep(
                title="Ejecutar y validar pruebas",
                description="Ejecutar la suite de pruebas y verificar cobertura",
                estimated_duration=300.0,
                dependencies=[None],
                required_agents=["tester_agent"],
                metadata={"phase": "execution"},
            ),
        ]


# ---------------------------------------------------------------------------
# General-purpose fallback
# ---------------------------------------------------------------------------


class GeneralTemplate(PlanTemplate):
    """Fallback template when no specific task type matches."""

    @property
    def task_type(self) -> str:
        return "general"

    def generate_steps(self, context: PlanningContext) -> list[PlanStep]:
        return [
            PlanStep(
                title="Analizar objetivo",
                description=f"Analizar el objetivo: {context.goal}",
                estimated_duration=300.0,
                required_agents=["research_agent"],
                metadata={"phase": "analysis"},
            ),
            PlanStep(
                title="Planificar ejecución",
                description="Definir los pasos necesarios",
                estimated_duration=300.0,
                dependencies=[None],
                required_agents=["backend_agent"],
                metadata={"phase": "planning"},
            ),
            PlanStep(
                title="Ejecutar tarea",
                description=f"Ejecutar la tarea principal: {context.goal}",
                estimated_duration=900.0,
                dependencies=[None],
                required_agents=context.suggested_agents or ["backend_agent"],
                metadata={"phase": "execution"},
            ),
            PlanStep(
                title="Verificar resultado",
                description="Confirmar que el objetivo fue cumplido",
                estimated_duration=300.0,
                dependencies=[None],
                required_agents=["tester_agent"],
                metadata={"phase": "verification"},
            ),
        ]


# ---------------------------------------------------------------------------
# Template registry helper
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATES: list[PlanTemplate] = [
    DevelopmentTaskTemplate(),
    BugFixTemplate(),
    ResearchTemplate(),
    DocumentationTemplate(),
    AnalysisTemplate(),
    DevOpsTemplate(),
    TestingTemplate(),
    GeneralTemplate(),
]


def get_template(task_type: str) -> PlanTemplate:
    """Look up a template by *task_type*.

    Falls back to :class:`GeneralTemplate` if no match is found.
    """
    for tmpl in _DEFAULT_TEMPLATES:
        if tmpl.task_type == task_type:
            return tmpl
    return GeneralTemplate()


def all_templates() -> dict[str, PlanTemplate]:
    """Return a mapping of all registered template types."""
    return {tmpl.task_type: tmpl for tmpl in _DEFAULT_TEMPLATES}
