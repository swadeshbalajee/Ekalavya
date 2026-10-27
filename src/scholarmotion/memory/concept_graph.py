"""Prerequisite graph of lesson concepts."""

from __future__ import annotations

from collections import deque


class ConceptGraph:
    def __init__(self):
        self.edges: dict[str, list[tuple[str, str]]] = {}

    def add_edge(self, source: str, target: str, edge_type: str) -> None:
        self.edges.setdefault(source.lower(), []).append((target.lower(), edge_type.upper()))

    def prerequisites(self, target: str, known: set[str]) -> list[str]:
        known_lower = {item.lower() for item in known}
        reverse: dict[str, list[str]] = {}
        for source, edges in self.edges.items():
            for destination, kind in edges:
                if kind == "PREREQUISITE_OF":
                    reverse.setdefault(destination, []).append(source)
        ordered: list[str] = []
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited or node in known_lower:
                return
            visited.add(node)
            for prerequisite in reverse.get(node, []):
                visit(prerequisite)
                if prerequisite not in known_lower and prerequisite not in ordered:
                    ordered.append(prerequisite)

        visit(target.lower())
        return ordered

    def expand(self, seeds: list[str], depth: int = 1) -> list[str]:
        found = {seed.lower() for seed in seeds}
        queue = deque((seed.lower(), 0) for seed in seeds)
        while queue:
            node, level = queue.popleft()
            if level >= depth:
                continue
            for neighbor, _ in self.edges.get(node, []):
                if neighbor not in found:
                    found.add(neighbor)
                    queue.append((neighbor, level + 1))
        return sorted(found)
