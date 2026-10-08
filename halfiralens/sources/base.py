# -*- coding: utf-8 -*-
"""Base class for Half IraLens specialized sources.

A *source* is one Internet platform (or the open web) with one or more
ordered backends. Backends may be APIs, CLIs, remote reader services, or the
shared browser engine — the source decides internally; callers see only
unified operations returning `Artifact`s.

Multi-backend routing semantics (inherited from the capability-layer study):
``backends`` is an ordered candidate list; ``health()`` probes candidates and
sets ``active_backend`` to whatever can actually serve right now.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..errors import OperationUnsupportedError
from ..model import Artifact

if TYPE_CHECKING:  # pragma: no cover
    from ..core import Context


@dataclass
class SourceHealth:
    status: str                      # ok | warn | off | error
    message: str
    active_backend: Optional[str] = None
    hint: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"status": self.status, "message": self.message}
        if self.active_backend:
            data["active_backend"] = self.active_backend
        if self.hint:
            data["hint"] = self.hint
        return data


class Source(ABC):  # noqa: B024 - concrete defaults; subclasses override what they support
    """One specialized Internet source with unified operations.

    Subclasses declare identity and capability with plain class attributes
    (name, description, backends, tier, operations); per-instance state is
    just `active_backend`.
    """

    name: str = ""
    description: str = ""
    backends: List[str] = []
    tier: int = 0                    # 0 = zero-config, 1 = free setup, 2 = login needed
    operations: Dict[str, Dict[str, Any]] = {}

    def __init__(self) -> None:
        self.active_backend: Optional[str] = None

    def can_handle(self, url: str) -> bool:
        """Whether this source natively reads this URL."""
        return False

    def health(self, context: "Context") -> SourceHealth:
        self.active_backend = self.backends[0] if self.backends else "built-in"
        return SourceHealth("ok", ", ".join(self.backends) or "built-in", self.active_backend)

    def fetch(self, op: str, params: Dict[str, Any], context: "Context") -> Any:
        raise OperationUnsupportedError(
            f"source '{self.name}' does not support operation '{op}'",
            hint=f"supported: {', '.join(sorted(self.operations)) or 'none'}",
        )

    def read_url(self, url: str, context: "Context", mode: str = "auto") -> Artifact:
        raise OperationUnsupportedError(f"source '{self.name}' cannot read arbitrary URLs")

    # ------------------------------------------------------------- helpers
    def require(self, params: Dict[str, Any], *keys: str) -> None:
        from ..errors import ExtractionError

        missing = [k for k in keys if not params.get(k)]
        if missing:
            raise ExtractionError(
                f"operation '{self.name}' is missing required parameter(s): {', '.join(missing)}"
            )

    def ordered_backends(self, context: "Context") -> List[str]:
        """Candidate backends honoring a `<name>_backend` config override."""
        candidates = list(self.backends)
        override = context.config.get(f"{self.name}_backend") if context.config else None
        if override:
            for i, backend in enumerate(candidates):
                if backend == override or backend.startswith(str(override)):
                    candidates.insert(0, candidates.pop(i))
                    break
        return candidates
