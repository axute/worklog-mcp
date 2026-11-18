"""Finding entries that were probably logged twice (same work, same project, close in time)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from .db import Entry

WINDOW_HOURS = 3
THRESHOLD = 0.75


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def similarity(a: str, b: str) -> float:
    """0..1: the higher of character similarity and word overlap of two summaries."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # "step 3 done: +139 entries" vs "step 4 done: +116 entries": same template, different work
    if set(re.findall(r"\d+", na)) != set(re.findall(r"\d+", nb)):
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    wa, wb = set(na.split()), set(nb.split())
    jaccard = len(wa & wb) / len(wa | wb) if min(len(wa), len(wb)) >= 3 else 0.0
    return max(ratio, jaccard)


def likely_same(a: Entry, b: Entry, window_hours: float = WINDOW_HOURS, threshold: float = THRESHOLD) -> float:
    """Similarity if a and b look like the same work logged twice, else 0."""
    if a.project != b.project or abs((a.ts - b.ts).total_seconds()) > window_hours * 3600:
        return 0.0
    s = similarity(a.summary, b.summary)
    return s if s >= threshold else 0.0


def find_groups(entries: Iterable[Entry], ignored: set[tuple[int, int]] = frozenset(),
                window_hours: float = WINDOW_HOURS, threshold: float = THRESHOLD) -> list[dict]:
    """Groups of likely duplicates, newest group first: [{"score": 0.93, "entries": [Entry, ...]}].
    `ignored` holds pairs (smaller id, larger id) marked as "not a duplicate"."""
    by_project: dict[str, list[Entry]] = {}
    for e in entries:
        by_project.setdefault(e.project, []).append(e)
    parent: dict[int, int] = {}
    score: dict[int, float] = {}
    items: dict[int, Entry] = {}

    def root(i: int) -> int:
        while parent.setdefault(i, i) != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for group in by_project.values():
        group.sort(key=lambda e: e.ts)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if (b.ts - a.ts).total_seconds() > window_hours * 3600:
                    break
                if (min(a.id, b.id), max(a.id, b.id)) in ignored:
                    continue
                s = likely_same(a, b, window_hours, threshold)
                if s:
                    items[a.id], items[b.id] = a, b
                    ra, rb = root(a.id), root(b.id)
                    parent[rb] = ra
                    score[ra] = max(score.get(ra, 0), score.pop(rb, 0), s)
    groups: dict[int, list[Entry]] = {}
    for i, e in items.items():
        groups.setdefault(root(i), []).append(e)
    out = [{"score": round(score.get(r, 0), 2), "entries": sorted(g, key=lambda e: e.ts)} for r, g in groups.items()]
    return sorted(out, key=lambda g: g["entries"][-1].ts, reverse=True)
