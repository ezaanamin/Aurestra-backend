"""Resolve which financial account a notification / SMS likely belongs to."""
from __future__ import annotations

import json
from typing import Any


def _keywords_json_to_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def match_account_for_notification(
    accounts: list[Any],
    title: str = "",
    text: str = "",
    package_name: str = "",
) -> Any | None:
    """
    Pick the best AccountBalance row using match_keywords (JSON list), display_name, and slug.
    Returns None if nothing scores.
    """
    hay = f"{title or ''} {text or ''} {package_name or ''}".lower()
    best = None
    best_score = 0

    for acc in accounts:
        score = 0
        for kw in _keywords_json_to_list(getattr(acc, "match_keywords", None)):
            kl = kw.lower()
            if kl and kl in hay:
                score += min(len(kl), 48)

        disp = (getattr(acc, "display_name", None) or "").strip()
        if disp and disp.lower() in hay:
            score += 8

        holder = (getattr(acc, "holder_name", None) or "").strip()
        if holder and holder.lower() in hay:
            score += min(len(holder), 48)

        slug = (getattr(acc, "source", None) or "").strip().lower()
        if slug and slug in hay:
            score += 4

        if score > best_score:
            best_score = score
            best = acc

    return best if best_score > 0 else None
