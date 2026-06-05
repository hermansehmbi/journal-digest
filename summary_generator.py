"""
Anesthesia Journal Digest — Deep Dive Summary Generator
========================================================
For each featured article, produce a structured "Deep Dive" summary grounded in
the article's full text (open access) or abstract (paywalled). Each summary has a
one-line "bottom line" plus six sections:

    What they did · Design · What they found · What it means ·
    Why it matters · Limitations

Uses the cheap/fast model (Haiku). One API call per article, run concurrently.
Per-article results are cached (summaries_cache.json) keyed by DOI/URL so a
re-run on the same articles does not regenerate or re-bill. We NEVER fabricate
numbers — abstract-only sources are summarized qualitatively.

Cost: ~$0.008/article on Haiku (~$0.07 for a 9-article digest).
"""

from __future__ import annotations

import os
import re
import json
import logging
import concurrent.futures
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = "summaries_cache.json"

# The six structured sections + the one-line takeaway. Order matters for display.
SECTION_FIELDS = [
    "bottom_line", "what_they_did", "design", "what_they_found",
    "what_it_means", "why_it_matters", "limitations",
]


def generate_summaries(articles: list, max_workers: int = 5) -> list[dict] | None:
    """Generate one Deep Dive summary per article. Returns a list of summary
    dicts (article metadata + the seven fields + an ``anchor``), or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from config import ANTHROPIC_API_KEY
        api_key = ANTHROPIC_API_KEY
    if not api_key:
        logger.error("No ANTHROPIC_API_KEY — skipping Deep Dive summaries.")
        return None
    if not articles:
        return None

    from config import CLAUDE_MODEL_FAST

    try:
        from fulltext_fetcher import get_article_text
    except Exception:
        get_article_text = None

    cache = _load_cache()
    results: list = [None] * len(articles)
    to_call: list[int] = []

    for i, art in enumerate(articles):
        key = _key(art)
        if key and key in cache:
            results[i] = dict(cache[key])
        else:
            to_call.append(i)

    def _work(i: int):
        art = articles[i]
        info = {"source": "abstract",
                "is_open_access": bool(art.get("is_open_access")),
                "text": art.get("abstract", "")}
        if get_article_text:
            try:
                info = get_article_text(art)
            except Exception as e:
                logger.warning(f"Full-text fetch failed for {art.get('url')}: {e}")
        summ = _summarize_one(api_key, CLAUDE_MODEL_FAST, art, info)
        return i, summ

    if to_call:
        workers = max(1, min(max_workers, len(to_call)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_work, i) for i in to_call]
            for fut in concurrent.futures.as_completed(futs):
                try:
                    i, summ = fut.result()
                except Exception as e:
                    logger.warning(f"Summary worker error: {e}")
                    continue
                if summ:
                    results[i] = summ
                    k = _key(articles[i])
                    if k:
                        cache[k] = summ
        _save_cache(cache)

    out = [r for r in results if r]
    # Assign display anchors in final order so the email teasers and the page
    # cards reference the same ids (#a1, #a2, …).
    for idx, s in enumerate(out, 1):
        s["anchor"] = f"a{idx}"
    logger.info(f"Generated {len(out)}/{len(articles)} Deep Dive summaries")
    return out or None


# ── Single-article summarization ─────────────────────────────────────────────

def _summarize_one(api_key: str, model: str, art: dict, info: dict) -> dict | None:
    """Call Haiku once to summarize a single article. Returns a merged dict."""
    import httpx

    source = info.get("source", "abstract")
    text = (info.get("text") or art.get("abstract", "")).strip()

    if source == "fulltext":
        access = ("OPEN-ACCESS FULL TEXT (Results/Discussion/Conclusions below). "
                  "You MAY quote real numbers that appear below.")
    elif text:
        access = ("ABSTRACT ONLY (paywalled). Base the summary ONLY on this abstract; "
                  "keep design and limitations general and DO NOT invent any statistic "
                  "not present below.")
    else:
        access = ("NO SOURCE TEXT available — keep every field qualitative and "
                  "general; do not invent specifics.")

    prompt = f"""You are writing a thorough, structured "Deep Dive" summary of ONE \
anesthesiology journal article for practicing anesthesiologists.

Article title: {art.get('title', '')}
Journal: {art.get('journal', '')} ({art.get('journal_abbr', '')})
Access: {access}

Source text:
{text[:6000]}

Write for a practicing generalist anesthesiologist, BALANCING the study's actual \
results with their bedside meaning. ALWAYS REPORT THE KEY NUMBERS — the primary \
outcome figures (event rates, effect size, risk/odds ratio, or absolute/relative \
change) and the main statistic if reported — and then translate them into plain \
clinical interpretation and what to do. Do NOT omit the results, but do NOT bury \
them under every confidence interval and p-value either: give the headline figures \
that matter, in plain terms, alongside the clinical takeaway.

Stay GROUNDED ONLY in the source text above. Never fabricate numbers, study \
designs, or findings; if the source is an abstract only, or a detail is not stated, \
say so (e.g. "not reported") rather than inventing it.

HARD RULE: never write the phrase "community anesthesiologist" (or "community \
anaesthetist" / "community anesthetist") anywhere in the output — it is internal \
audience guidance only and must not appear.

LENGTH: aim for roughly 320-420 words total — substantive but practical.

Respond with RAW JSON only — no markdown, no backticks — with EXACTLY these keys:
{{
  "bottom_line": "1-2 sentence takeaway that includes the single most important result (with its key number if reported) AND what it means (<=45 words).",
  "what_they_did": "The clinical question, the intervention or exposure, and the patients studied (2-3 sentences).",
  "design": "Study type, setting, size, comparator, and the main outcome — briefly (2-3 sentences; say 'not reported' where unclear).",
  "what_they_found": "Report the ACTUAL results with the real key numbers from the source — primary outcome figures and the main statistic, plus important secondary or safety findings. State numbers in plain terms but DO include them. If the source is an abstract with no numbers, say so and give the direction of effect (3-5 sentences).",
  "what_it_means": "Translate the result into what to actually do at the bedside — for which patients, and how it does or does not change management (3-4 sentences).",
  "why_it_matters": "Why this matters in everyday practice (1-2 sentences).",
  "limitations": "The main caution and who the results may not apply to (1-2 sentences)."
}}
Aim for about 320-420 words total. Plain text only in each field (no markdown)."""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = "".join(b.get("text", "") for b in data.get("content", [])
                      if b.get("type") == "text").strip()
        obj = _parse_obj(raw)
    except Exception as e:
        logger.warning(f"Summary generation failed for {art.get('url')}: {e}")
        return None

    out = {
        "title": art.get("title", ""),
        "journal": art.get("journal", ""),
        "journal_abbr": art.get("journal_abbr", ""),
        "url": art.get("url", ""),
        "authors": art.get("authors", ""),
        "date_str": art.get("date_str", ""),
        "impact_factor": art.get("impact_factor"),
        "is_open_access": info.get("is_open_access", bool(art.get("is_open_access"))),
        "source": source,
    }
    for k in SECTION_FIELDS:
        v = obj.get(k)
        out[k] = (v or "").strip() if isinstance(v, str) else ""
    # A summary with no bottom line and no findings is useless — reject it.
    if not out["bottom_line"] and not out["what_they_found"]:
        logger.warning(f"Empty summary discarded for {art.get('url')}")
        return None
    return out


def _parse_obj(raw: str) -> dict:
    """Parse a JSON object, tolerating stray markdown fences / surrounding text."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        i, j = raw.find("{"), raw.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(raw[i:j + 1])
        raise


# ── Cache ────────────────────────────────────────────────────────────────────

def _doi(article: dict) -> str:
    doi = (article.get("doi") or "").strip()
    if doi:
        return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I).lower()
    for field in ("url", "id"):
        m = re.search(r"(10\.\d{4,}/[^\s?#]+)", article.get(field, "") or "")
        if m:
            return m.group(1).lower()
    return ""


def _key(article: dict) -> str:
    doi = _doi(article)
    if doi:
        return "doi:" + doi
    url = article.get("url") or ""
    return "url:" + url if url else ""


def _load_cache() -> dict:
    p = Path(CACHE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict):
    try:
        Path(CACHE_FILE).write_text(json.dumps(cache), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not write {CACHE_FILE}: {e}")
