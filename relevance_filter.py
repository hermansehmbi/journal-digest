"""
Journal Digest — Relevance Filter
=================================
For specialties that draw on a few high-impact GENERAL journals (Tier 2), most
articles are off-topic. This module classifies each candidate article from such
a journal as RELEVANT or NOT_RELEVANT to the specialty, using a fast/cheap Claude
model, and keeps only the relevant ones.

It is called ONLY for journals where ``filter_required: true`` in the specialty
JSON. Dedicated specialty journals (Tier 1) are never filtered.

The classification prompt lives in the specialty JSON under
``relevance_filter_prompt`` (exposed as ``config.RELEVANCE_FILTER_PROMPT``).
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)

# Cap abstract length sent to the classifier to bound token cost (pennies/day).
_ABSTRACT_CHARS = 1200


def filter_articles(articles: list[dict], specialty_config=None,
                    prompt: str | None = None) -> list[dict]:
    """Keep only articles RELEVANT to the specialty.

    Parameters
    ----------
    articles : list[dict]
        Candidate articles from a general (filter_required) journal.
    specialty_config : module | dict | None
        Optional; the ``config`` module (or a dict) to read the prompt/model
        from. Defaults to the imported ``config`` module.
    prompt : str | None
        Optional explicit classification prompt; overrides the config value.

    Returns
    -------
    list[dict]
        The subset classified RELEVANT. On any failure the input list is
        returned unchanged (fail-open) so a classifier hiccup never silently
        drops a whole journal.
    """
    if not articles:
        return []

    cfg = specialty_config
    if cfg is None:
        import config as cfg

    filter_prompt = prompt or _get(cfg, "RELEVANCE_FILTER_PROMPT", "")
    if not filter_prompt:
        # No prompt configured → nothing to classify against; keep everything.
        logger.info("Relevance filter: no prompt configured — keeping all "
                    f"{len(articles)} articles")
        return articles

    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or _get(cfg, "ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("Relevance filter: no ANTHROPIC_API_KEY — keeping all "
                       f"{len(articles)} articles (fail-open)")
        return articles

    model = _get(cfg, "RELEVANCE_MODEL", "claude-haiku-4-5-20251001")

    kept = []
    for art in articles:
        try:
            verdict = _classify(api_key, model, filter_prompt, art)
        except Exception as e:
            logger.warning(f"Relevance filter error on '{art.get('title','')[:60]}' "
                           f"({e}); keeping it (fail-open)")
            kept.append(art)
            continue
        if verdict:
            kept.append(art)

    journal = articles[0].get("journal_abbr", "") or articles[0].get("journal", "")
    logger.info(f"Relevance filter [{journal}]: kept {len(kept)}/{len(articles)} "
                f"relevant ({model})")
    return kept


def _classify(api_key: str, model: str, filter_prompt: str, art: dict) -> bool:
    """Return True if the article is RELEVANT. Defaults to True (keep) if the
    model's answer is ambiguous, so we never silently drop borderline items."""
    import httpx

    title = (art.get("title") or "").strip()
    abstract = (art.get("abstract") or "").strip()[:_ABSTRACT_CHARS]
    user = (f"{filter_prompt}\n\n"
            f"ARTICLE TITLE: {title}\n"
            f"ABSTRACT: {abstract or '(no abstract available)'}\n\n"
            f"Answer with ONLY one word: RELEVANT or NOT_RELEVANT")

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text").strip().upper()
    # Be strict about NOT_RELEVANT; anything else (including ambiguity) keeps it.
    return "NOT_RELEVANT" not in text


def _get(cfg, name, default):
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)
