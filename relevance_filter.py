"""
Journal Digest — Relevance Filter
=================================
For specialties that draw on a few high-impact GENERAL journals (Tier 2), most
articles are off-topic. This module classifies each candidate article from such
a journal as RELEVANT or NOT_RELEVANT to the specialty, using a fast/cheap Claude
model, and keeps only the relevant ones.

It is called ONLY for journals where ``filter_required: true`` in the specialty
JSON. Dedicated specialty journals (Tier 1) are never filtered.

Classification is BATCHED — one API call rates ~12 articles at once — so a
60-article journal costs ~5 calls instead of 60 (avoids rate-limit storms). The
criteria prompt lives in the specialty JSON under ``relevance_filter_prompt``
(exposed as ``config.RELEVANCE_FILTER_PROMPT``).
"""

from __future__ import annotations

import os
import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Articles classified per API call, and abstract chars sent per article.
_BATCH_SIZE = 12
_ABSTRACT_CHARS = 600


# ── Verdict cache (so repeat scans/previews don't re-pay) ────────────────────

def _cache_file() -> str:
    try:
        import config
        return config.cache_path("relevance_cache.json")
    except Exception:
        return "relevance_cache.json"


def _akey(art: dict) -> str:
    doi = (art.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return "url:" + (art.get("url") or "")


def _load_verdicts() -> dict:
    p = Path(_cache_file())
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_verdicts(cache: dict):
    try:
        Path(_cache_file()).write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


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
        The subset classified RELEVANT. On any failure the affected articles are
        kept (fail-open) so a classifier hiccup never silently drops a journal.
    """
    if not articles:
        return []

    cfg = specialty_config
    if cfg is None:
        import config as cfg

    filter_prompt = prompt or _get(cfg, "RELEVANCE_FILTER_PROMPT", "")
    if not filter_prompt:
        logger.info("Relevance filter: no prompt configured — keeping all "
                    f"{len(articles)} articles")
        return articles

    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or _get(cfg, "ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("Relevance filter: no ANTHROPIC_API_KEY — keeping all "
                       f"{len(articles)} articles (fail-open)")
        return articles

    model = _get(cfg, "RELEVANCE_MODEL", "claude-haiku-4-5-20251001")

    # Only classify articles we haven't judged before — cached verdicts are free.
    cache = _load_verdicts()
    uncached = [a for a in articles if _akey(a) not in cache]
    classified = 0
    for start in range(0, len(uncached), _BATCH_SIZE):
        batch = uncached[start:start + _BATCH_SIZE]
        try:
            verdicts = _classify_batch(api_key, model, filter_prompt, batch)
        except Exception as e:
            # Keep this batch (fail-open) but don't cache, so it retries later.
            logger.warning(f"Relevance filter batch error ({e}); keeping "
                           f"{len(batch)} (fail-open, uncached)")
            continue
        for i, art in enumerate(batch):
            cache[_akey(art)] = ("NOT_RELEVANT" if verdicts.get(i) == "NOT_RELEVANT"
                                 else "RELEVANT")
        classified += len(batch)
    if classified:
        _save_verdicts(cache)

    # Keep everything not explicitly NOT_RELEVANT (uncached/error -> kept).
    kept = [a for a in articles if cache.get(_akey(a)) != "NOT_RELEVANT"]
    journal = articles[0].get("journal_abbr", "") or articles[0].get("journal", "")
    logger.info(f"Relevance filter [{journal}]: kept {len(kept)}/{len(articles)} "
                f"relevant ({classified} newly classified, "
                f"{len(articles) - len(uncached)} from cache; {model})")
    return kept


def _classify_batch(api_key: str, model: str, filter_prompt: str,
                    batch: list[dict]) -> dict[int, str]:
    """Classify a batch of articles in ONE call. Returns {index: "RELEVANT"|
    "NOT_RELEVANT"}. Missing indices are treated as RELEVANT by the caller."""
    import httpx

    lines = []
    for i, art in enumerate(batch):
        title = (art.get("title") or "").strip()
        abstract = (art.get("abstract") or "").strip()[:_ABSTRACT_CHARS]
        lines.append(f"[{i}] TITLE: {title}\n    ABSTRACT: {abstract or '(none)'}")
    listing = "\n\n".join(lines)

    user = (
        f"{filter_prompt}\n\n"
        f"Classify EACH of the following {len(batch)} articles (numbered from 0). "
        f"Respond with ONLY raw JSON, no markdown:\n"
        f'{{"results": [{{"n": 0, "verdict": "RELEVANT"}}, ...]}}\n'
        f'where verdict is exactly "RELEVANT" or "NOT_RELEVANT".\n\n'
        f"ARTICLES:\n{listing}"
    )

    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 40 + len(batch) * 20,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    out: dict[int, str] = {}
    try:
        parsed = json.loads(text)
        for r in parsed.get("results", []):
            n = int(r.get("n"))
            verdict = str(r.get("verdict", "")).upper()
            out[n] = "NOT_RELEVANT" if "NOT_RELEVANT" in verdict else "RELEVANT"
    except (json.JSONDecodeError, ValueError, TypeError):
        # Fallback: scrape "n: NOT_RELEVANT" style lines; anything unscraped is
        # left out of `out`, so the caller keeps it (fail-open).
        for m in re.finditer(r"(\d+)\D{0,12}(NOT_RELEVANT|RELEVANT)", text.upper()):
            out[int(m.group(1))] = m.group(2)
    return out


def _get(cfg, name, default):
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)
