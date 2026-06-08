"""
Journal Digest — Article Selection (accessibility-scored)
=========================================================
Picks the articles to feature each run. One path: score every candidate over the
look-back window and take the best N overall, preferring articles whose full text
we can actually retrieve, never repeating an already-sent article.

Scoring (per article):
  base               +1
  retrievable full text  +SCORE_FULLTEXT_WEIGHT   (0 disables the bias)
  open access            +2
  Tier-1 (dedicated)     +1
  recency            +2 (<=7d) / +1 (<=14d)
Ranking: score, then impact factor, then date. An optional ``max_per_journal``
cap stops one high-impact journal from monopolizing the digest.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _tier1_abbrs() -> set:
    from config import JOURNALS
    return {j["abbreviation"] for j in JOURNALS if not j.get("filter_required")}


def _as_date(d):
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d)
        except ValueError:
            return None
    return d


def score_article(art: dict, avail: dict, now: datetime, tier1: set) -> dict:
    """Return {"score": int, "parts": {...}} for one article."""
    from config import SCORE_FULLTEXT_WEIGHT
    parts = {"base": 1}
    if avail.get("has_fulltext") and SCORE_FULLTEXT_WEIGHT:
        parts["fulltext"] = SCORE_FULLTEXT_WEIGHT
    if avail.get("is_oa") or art.get("is_open_access"):
        parts["open_access"] = 2
    if art.get("journal_abbr") in tier1:
        parts["tier1"] = 1
    d = _as_date(art.get("date"))
    age = (now - d).days if d else 999
    if age <= 7:
        parts["recency"] = 2
    elif age <= 14:
        parts["recency"] = 1
    return {"score": sum(parts.values()), "parts": parts}


def select_top_scored(articles: list, avail_map: dict, sent_keys: set,
                      top_n: int = 5, max_per_journal: int | None = None):
    """Score every (not-already-sent) article and return (top_n, all_ranked).

    Each returned article is annotated with ``_score``, ``_score_parts`` and
    ``_avail``. Ranking: score, then impact factor, then recency.
    """
    import fulltext_resolver as fr
    now = datetime.now()
    tier1 = _tier1_abbrs()
    ranked = []
    for a in articles:
        if fr.key(a) in sent_keys:
            continue
        av = avail_map.get(fr.key(a), {})
        sc = score_article(a, av, now, tier1)
        a["_score"] = sc["score"]
        a["_score_parts"] = sc["parts"]
        a["_avail"] = av
        ranked.append(a)

    ranked.sort(key=lambda a: (a["_score"], a.get("impact_factor", 0) or 0,
                               _as_date(a.get("date")) or datetime.min), reverse=True)

    if max_per_journal:
        per: dict = {}
        top = []
        for a in ranked:
            j = a.get("journal_abbr", "")
            if per.get(j, 0) >= max_per_journal:
                continue
            per[j] = per.get(j, 0) + 1
            top.append(a)
            if len(top) >= top_n:
                break
    else:
        top = ranked[:top_n]
    return top, ranked


def select_for_digest(all_articles: list, sent_keys: set) -> list:
    """Full selection pipeline: dedup, resolve missing DOIs, check full-text
    availability (Unpaywall + PMC, cached), score, and return the featured set
    per the active specialty's config (top-N, per-journal cap)."""
    import fulltext_resolver as fr
    from config import DIGEST_TOP_N, MAX_PER_JOURNAL

    seen, uniq = set(), []
    for a in all_articles:
        k = fr.key(a)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)

    fr.resolve_dois(uniq)              # fill DOIs missing from publisher feeds
    avail = fr.availability_map(uniq)  # Unpaywall + PMC (cached)
    top, _ = select_top_scored(uniq, avail, sent_keys, top_n=DIGEST_TOP_N,
                               max_per_journal=(MAX_PER_JOURNAL or None))
    ft = sum(1 for a in top if a.get("_avail", {}).get("has_fulltext"))
    logger.info(f"Scored selection: {len(top)} articles ({ft} with full text), "
                f"excluding {sum(1 for a in uniq if fr.key(a) in sent_keys)} already-sent")
    return top
