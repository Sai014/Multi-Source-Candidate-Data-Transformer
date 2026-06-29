"""Source adapter protocol and registry.

Adapters are the impure shell: they read a file and emit :class:`Claim` objects.
Implementations register themselves so detection can route by capability rather
than hardcoded branches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.domain.enums import SourceType
from app.domain.models import Claim


class SourceAdapter(Protocol):
    """Structural type for a source adapter.

    ``source_type`` labels claims (and any quarantine record) with their origin.
    """

    source_type: SourceType

    def can_handle(self, path: Path) -> bool:
        """Return whether this adapter recognizes the input at ``path``."""
        ...

    def extract(self, path: Path) -> list[Claim]:
        """Read ``path`` and return the claims it asserts. May perform I/O."""
        ...


ADAPTER_REGISTRY: list[SourceAdapter] = []


def register_adapter(adapter: SourceAdapter) -> None:
    """Register an adapter for use by the default detection routing."""
    ADAPTER_REGISTRY.append(adapter)
