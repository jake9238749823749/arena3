"""Opportunity-cost research on rejected FRS candidates.

We cannot invent the P&L of a trade we did not take. We *can* record
why candidates died, how their scores compared to the winner, and
whether losers cluster in time with winners.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from engine.portfolio import Trade


def analyze_rejected(rejected: list[dict], trades: list[Trade] | None = None) -> dict[str, Any]:
    reasons = Counter(str(r.get("reason") or "unknown") for r in rejected)
    scores = [float(r["score"]) for r in rejected if r.get("score") is not None]
    winner_scores = [
        float(t.signal_meta["score"])
        for t in (trades or [])
        if t.signal_meta.get("score") is not None
    ]
    outranked = [r for r in rejected if r.get("reason") == "outranked"]
    blocked = [r for r in rejected if r.get("reason") == "blocked_by_position"]
    return {
        "n_rejected": len(rejected),
        "by_reason": dict(reasons),
        "n_outranked": len(outranked),
        "n_blocked_by_position": len(blocked),
        "rejected_score_mean": sum(scores) / len(scores) if scores else None,
        "selected_score_mean": sum(winner_scores) / len(winner_scores) if winner_scores else None,
        "outranked_vs_winner": [
            {
                "ts": r.get("ts"),
                "loser": r.get("name"),
                "winner": r.get("winner"),
                "loser_score": r.get("score"),
            }
            for r in outranked[:50]
        ],
    }
