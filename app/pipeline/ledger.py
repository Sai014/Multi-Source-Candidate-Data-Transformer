"""The claim ledger: an append-only record of every claim made by every source.

The ledger is the narrow waist of the hourglass - all sources fan in here before
fusion fans back out. It is append-only and exposes only a read-only view, so the
audit trail can never be silently rewritten. Claims themselves are frozen, so
appended claims cannot be mutated either.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from app.domain.models import Claim


class ClaimLedger:
    """An append-only collection of :class:`Claim` records."""

    def __init__(self) -> None:
        self._claims: list[Claim] = []

    def append(self, claim: Claim) -> None:
        """Append a single claim."""
        self._claims.append(claim)

    def extend(self, claims: Iterable[Claim]) -> None:
        """Append many claims, preserving order."""
        self._claims.extend(claims)

    @property
    def claims(self) -> tuple[Claim, ...]:
        """Return an immutable snapshot of all claims, in insertion order."""
        return tuple(self._claims)

    def __len__(self) -> int:
        return len(self._claims)

    def __iter__(self) -> Iterator[Claim]:
        return iter(self._claims)
