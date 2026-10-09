# -*- coding: utf-8 -*-
"""Provenance graph: how every statement traces back to queries and sources.

Node kinds: question, query, source, claim, contradiction, statement.
Relations:  issued (question->query), returned (query->source), states
(source->claim), supports (claim->statement), conflicts_with (claim->claim).

The graph is serialized into the report. `upstream()` answers "why do we say
this?" and `downstream()` answers "what did this source contribute to?".
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Set


class ProvenanceGraph:
    def __init__(self) -> None:
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: List[Dict[str, str]] = []

    def node(self, node_id: str, kind: str, **attrs: Any) -> None:
        self.nodes.setdefault(node_id, {"id": node_id, "kind": kind, **attrs})

    def edge(self, src: str, dst: str, relation: str) -> None:
        item = {"src": src, "dst": dst, "relation": relation}
        if item not in self.edges:
            self.edges.append(item)

    def _walk(self, start: str, forward: bool) -> List[str]:
        seen: Set[str] = {start}
        order: List[str] = []
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for e in self.edges:
                nxt = None
                if forward and e["src"] == current:
                    nxt = e["dst"]
                elif not forward and e["dst"] == current:
                    nxt = e["src"]
                if nxt and nxt not in seen:
                    seen.add(nxt)
                    order.append(nxt)
                    queue.append(nxt)
        return order

    def upstream(self, node_id: str) -> List[str]:
        return self._walk(node_id, forward=False)

    def downstream(self, node_id: str) -> List[str]:
        return self._walk(node_id, forward=True)

    def to_dict(self) -> Dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges)}
