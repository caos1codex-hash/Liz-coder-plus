"""DAG (Directed Acyclic Graph) para workflows (Sprint 2.2).

Operaciones:

- `validate()`: detecta ciclos, dependencias rotas y pasos huérfanos.
- `topological_order()`: devuelve un orden topológico respetando prioridad.
- `ready_steps(completed)`: devuelve steps cuyas dependencias están completas.
- `ancestors(step_id)` / `descendants(step_id)`.
- `has_path(from_id, to_id)`.
- `subgraph(step_ids)`.

El DAG es **immutable** después de `validate()`. Cualquier mutación
crea un nuevo DAG (estructural sharing del dict de steps).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Resultado de validación
# ----------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Resultado de validar un DAG.

    Attributes:
        ok:              True si el DAG es válido.
        cycles:          Lista de ciclos detectados (cada uno una lista de step_ids).
        broken_deps:     Lista de (step_id, dep_id) donde dep_id no existe.
        orphan_steps:    Lista de step_ids sin dependencias y sin sucesores
                          (candidatos a "punto muerto" si el workflow los
                          espera). En workflows reales esto puede ser válido,
                          pero se reporta para inspección.
        warnings:        Lista de advertencias menores.
    """

    ok: bool = True
    cycles: list[list[str]] = field(default_factory=list)
    broken_deps: list[tuple[str, str]] = field(default_factory=list)
    orphan_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "cycles": [list(c) for c in self.cycles],
            "broken_deps": [list(d) for d in self.broken_deps],
            "orphan_steps": list(self.orphan_steps),
            "warnings": list(self.warnings),
        }


# ----------------------------------------------------------------------
# DAG
# ----------------------------------------------------------------------


@dataclass
class DAG:
    """Directed Acyclic Graph de steps.

    Args:
        steps: Dict {step_id: set(dep_ids)}.
        priorities: Dict opcional {step_id: int} (menor = más prioritario).
                    Se usa sólo como desempate en topological_order.
    """

    steps: dict[str, set[str]] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def add_step(
        self,
        step_id: str,
        depends_on: Iterable[str] | None = None,
        *,
        priority: int = 0,
    ) -> None:
        """Añade un step al DAG."""
        self.steps[step_id] = set(depends_on or [])
        self.priorities[step_id] = priority

    def remove_step(self, step_id: str) -> None:
        """Elimina un step y limpia referencias en dependencias."""
        self.steps.pop(step_id, None)
        self.priorities.pop(step_id, None)
        for deps in self.steps.values():
            deps.discard(step_id)

    def add_dependency(self, step_id: str, dep_id: str) -> None:
        """Añade una dependencia `dep_id` a `step_id`."""
        self.steps.setdefault(step_id, set()).add(dep_id)
        self.priorities.setdefault(step_id, 0)
        self.priorities.setdefault(dep_id, 0)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def step_ids(self) -> list[str]:
        return list(self.steps.keys())

    def dependencies_of(self, step_id: str) -> set[str]:
        return set(self.steps.get(step_id, set()))

    def dependents_of(self, step_id: str) -> set[str]:
        """Devuelve los steps que dependen de `step_id`."""
        return {s for s, deps in self.steps.items() if step_id in deps}

    def has_step(self, step_id: str) -> bool:
        return step_id in self.steps

    # ------------------------------------------------------------------
    # Validación
    # ------------------------------------------------------------------

    def validate(self) -> ValidationResult:
        """Valida el DAG: ciclos, dependencias rotas, huérfanos."""
        result = ValidationResult()

        # 1. Dependencias rotas: dependencias que apuntan a steps inexistentes.
        for step_id, deps in self.steps.items():
            for dep in deps:
                if dep not in self.steps:
                    result.broken_deps.append((step_id, dep))
                    result.ok = False

        # 2. Ciclos (DFS con colores).
        cycles = self._find_cycles()
        if cycles:
            result.cycles = cycles
            result.ok = False

        # 3. Pasos huérfanos: sin dependencias y sin sucesores.
        for step_id, deps in self.steps.items():
            if not deps and not self.dependents_of(step_id):
                # Si el workflow tiene un solo step, no es huérfano; es válido.
                if len(self.steps) > 1:
                    result.orphan_steps.append(step_id)
                    result.warnings.append(
                        f"Step '{step_id}' has no dependencies and no dependents"
                    )

        return result

    def _find_cycles(self) -> list[list[str]]:
        """Detecta ciclos usando DFS con 3 colores (white/gray/black)."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {s: WHITE for s in self.steps}
        cycles: list[list[str]] = []
        path: list[str] = []

        def visit(node: str) -> bool:
            """Devuelve True si se encontró un ciclo."""
            color[node] = GRAY
            path.append(node)
            for dep in self.steps.get(node, set()):
                if dep not in color:
                    continue  # dep roto; ya reportado en validate()
                if color[dep] == GRAY:
                    # Ciclo: extraer el subpath desde `dep` hasta `node`.
                    idx = path.index(dep)
                    cycles.append(list(path[idx:]) + [dep])
                    return True
                if color[dep] == WHITE:
                    if visit(dep):
                        # No retornar: queremos encontrar TODOS los ciclos.
                        pass
            color[node] = BLACK
            path.pop()
            return False

        for step_id in self.steps:
            if color[step_id] == WHITE:
                visit(step_id)

        # Deduplicar ciclos (mismo ciclo puede aparecer varias veces).
        seen: set[tuple[str, ...]] = set()
        unique: list[list[str]] = []
        for c in cycles:
            # Normalizar: rotar para empezar por el menor id.
            rotated = c[:-1]
            if not rotated:
                continue
            min_idx = rotated.index(min(rotated))
            normalized = tuple(rotated[min_idx:] + rotated[:min_idx])
            if normalized not in seen:
                seen.add(normalized)
                unique.append(list(normalized) + [normalized[0]])
        return unique

    # ------------------------------------------------------------------
    # Orden topológico
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """Devuelve un orden topológico (Kahn's algorithm).

        Empata por prioridad (menor primero) y luego por orden alfabético.
        Lanza ValueError si hay ciclo.
        """
        v = self.validate()
        if v.cycles:
            raise ValueError(f"Cannot topologically sort DAG with cycles: {v.cycles}")

        # In-degree de cada nodo (contando sólo deps existentes).
        in_degree: dict[str, int] = {
            s: sum(1 for d in deps if d in self.steps)
            for s, deps in self.steps.items()
        }
        # Nodos con in-degree 0.
        ready: list[str] = sorted(
            [s for s, deg in in_degree.items() if deg == 0],
            key=lambda s: (self.priorities.get(s, 0), s),
        )
        result: list[str] = []
        while ready:
            node = ready.pop(0)
            result.append(node)
            for dependent in sorted(self.dependents_of(node)):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    # Insertar manteniendo el orden.
                    inserted = False
                    for i, existing in enumerate(ready):
                        if (
                            self.priorities.get(dependent, 0) < self.priorities.get(existing, 0)
                            or (
                                self.priorities.get(dependent, 0)
                                == self.priorities.get(existing, 0)
                                and dependent < existing
                            )
                        ):
                            ready.insert(i, dependent)
                            inserted = True
                            break
                    if not inserted:
                        ready.append(dependent)
        if len(result) != len(self.steps):
            raise ValueError("Topological sort incomplete; cycle may exist")
        return result

    # ------------------------------------------------------------------
    # Steps listos para ejecutar
    # ------------------------------------------------------------------

    def ready_steps(
        self,
        completed: set[str],
        *,
        skip: set[str] | None = None,
    ) -> list[str]:
        """Devuelve steps cuyas dependencias están completas.

        Args:
            completed: Conjunto de step_ids completados.
            skip:      Conjunto de step_ids a excluir (en ejecución o cancelados).
        """
        skip = skip or set()
        result: list[str] = []
        for step_id, deps in self.steps.items():
            if step_id in skip or step_id in completed:
                continue
            # Todas las dependencias deben estar completadas.
            if all(d in completed for d in deps if d in self.steps):
                result.append(step_id)
        # Ordenar por prioridad y nombre.
        result.sort(key=lambda s: (self.priorities.get(s, 0), s))
        return result

    # ------------------------------------------------------------------
    # Ancestros / descendientes
    # ------------------------------------------------------------------

    def ancestors(self, step_id: str) -> set[str]:
        """Devuelve todos los ancestros transitivos de `step_id`."""
        result: set[str] = set()
        stack = list(self.dependencies_of(step_id))
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(self.dependencies_of(current))
        return result

    def descendants(self, step_id: str) -> set[str]:
        """Devuelve todos los descendientes transitivos de `step_id`."""
        result: set[str] = set()
        stack = list(self.dependents_of(step_id))
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            stack.extend(self.dependents_of(current))
        return result

    def has_path(self, from_id: str, to_id: str) -> bool:
        """Devuelve True si existe un camino `from_id → ... → to_id`."""
        if from_id == to_id:
            return True
        return to_id in self.descendants(from_id)

    # ------------------------------------------------------------------
    # Subgraph
    # ------------------------------------------------------------------

    def subgraph(self, step_ids: Iterable[str]) -> DAG:
        """Devuelve un nuevo DAG restringido a `step_ids`."""
        keep = set(step_ids)
        new_steps: dict[str, set[str]] = {}
        new_priorities: dict[str, int] = {}
        for s in keep:
            if s in self.steps:
                new_steps[s] = {d for d in self.steps[s] if d in keep}
                new_priorities[s] = self.priorities.get(s, 0)
        return DAG(steps=new_steps, priorities=new_priorities)

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "steps": {s: sorted(d) for s, d in self.steps.items()},
            "priorities": dict(self.priorities),
        }

    @classmethod
    def from_dict(cls, data: dict) -> DAG:
        steps = {s: set(d) for s, d in data.get("steps", {}).items()}
        priorities = dict(data.get("priorities", {}))
        return cls(steps=steps, priorities=priorities)


__all__ = ["DAG", "ValidationResult"]
