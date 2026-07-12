"""Optional utility ranking over already-authorized SkillCards only."""

from __future__ import annotations

from collections.abc import Sequence

from fleet_rlm_clean.skills.models import SkillCard


def rank_authorized_cards(
    cards: Sequence[SkillCard],
    query: str | None = None,
) -> tuple[SkillCard, ...]:
    """Return a reordered view of *cards* only — never expands the candidate set.

    Empty or missing query preserves input order. Scoring is a simple
    case-insensitive substring match on name and description.
    """
    items = list(cards)
    if not query or not query.strip():
        return tuple(items)

    q = query.strip().lower()
    tokens = [t for t in q.split() if t]

    def score(card: SkillCard) -> tuple[int, str]:
        hay = f"{card.name} {card.description}".lower()
        hits = sum(1 for t in tokens if t in hay)
        # Prefer more hits, then stable name order among ties
        return (-hits, card.name.lower())

    items.sort(key=score)
    return tuple(items)
