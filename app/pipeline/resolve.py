"""Identity resolution: cluster source records into candidates.

Records are joined when they share a deterministic match key - a normalized
email, an E.164 phone, or a canonical profile URL - using union-find. There is
no fuzzy name matching (it invents links that are not there). Clustering is pure
and its output order is stable.

A *record* is the claims extracted from one source. Co-occurrence within a record
is what lets key-less claims (a name, a skill) travel with the keyed claims (an
email, a phone) of the same person, so resolution operates on records rather than
loose claims.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import Claim, Links
from app.normalize import normalize

MatchKey = tuple[str, str]


def _links_urls(links: Links) -> list[str]:
    urls = [links.linkedin, links.github, links.portfolio, *links.other]
    return [url for url in urls if url]


def _claim_keys(claim: Claim) -> set[MatchKey]:
    """Derive the match keys a single claim contributes (possibly none)."""
    keys: set[MatchKey] = set()
    value = claim.value
    if claim.field == "emails" and isinstance(value, str):
        result = normalize("email", value)
        if result.ok and isinstance(result.value, str):
            keys.add(("email", result.value))
    elif claim.field == "phones" and isinstance(value, str):
        result = normalize("phone_e164", value)
        if result.ok and isinstance(result.value, str):
            keys.add(("phone", result.value))
    elif claim.field == "links" and isinstance(value, Links):
        for url in _links_urls(value):
            result = normalize("url_link", url)
            if result.ok and isinstance(result.value, str):
                keys.add(("url", result.value))
    return keys


def _record_keys(record: Sequence[Claim]) -> set[MatchKey]:
    keys: set[MatchKey] = set()
    for claim in record:
        keys |= _claim_keys(claim)
    return keys


class _UnionFind:
    """Minimal union-find; unions attach to the smaller index for stable roots."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, left: int, right: int) -> None:
        root_left, root_right = self.find(left), self.find(right)
        if root_left != root_right:
            self._parent[max(root_left, root_right)] = min(root_left, root_right)


def cluster(records: Sequence[Sequence[Claim]]) -> list[list[Claim]]:
    """Group records that share any match key into candidate claim clusters.

    Returns one flat claim list per candidate. Cluster order follows the smallest
    contributing record index; within a cluster, claims keep record then claim
    order - so identical input yields identical output.
    """
    union_find = _UnionFind(len(records))
    first_index_for_key: dict[MatchKey, int] = {}
    for index, record in enumerate(records):
        for key in sorted(_record_keys(record)):
            owner = first_index_for_key.setdefault(key, index)
            if owner != index:
                union_find.union(owner, index)

    grouped: dict[int, list[int]] = {}
    for index in range(len(records)):
        grouped.setdefault(union_find.find(index), []).append(index)

    clusters: list[list[Claim]] = []
    for root in sorted(grouped):
        claims: list[Claim] = []
        for index in sorted(grouped[root]):
            claims.extend(records[index])
        clusters.append(claims)
    return clusters
