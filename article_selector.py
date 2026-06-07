"""
Anesthesia Journal Digest — Smart Article Selection
====================================================
Pick the ONE most clinically relevant article per journal for a practicing
generalist anesthesiologist.

- OPEN ACCESS ONLY: paywalled articles are never featured.
- A SINGLE batched Claude API call (cheap model) chooses the best article for
  every journal at once (instead of one call per journal).

Selection priorities (high → low):
  HIGH  randomized controlled trials, systematic reviews, meta-analyses,
        clinical practice guidelines, consensus statements, and topics with
        direct bedside impact (analgesia, airway, obstetric anesthesia,
        perioperative management, regional techniques, patient safety)
  LOW   basic-science / animal studies (e.g. mouse models), bibliometric or
        methodology papers, editorials, and letters

Falls back to a sensible heuristic if the API is unavailable.
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_MAX_CANDIDATES_PER_JOURNAL = 8   # cap sent to the API to bound token cost
_ABSTRACT_CHARS = 280             # truncate abstracts in the selection prompt

# Built-in anesthesia editor guidance, used when the specialty JSON does not
# define its own article_selector_prompt (keeps anesthesia output unchanged).
_DEFAULT_SELECTOR_INTRO = (
    "You are the editor of a digest for a practicing generalist "
    "anesthesiologist (general OR lists, obstetrics, regional, acute pain). For "
    "EACH journal below, pick the ONE single most clinically relevant article "
    "for that reader by its number.\n\n"
    "PRIORITIZE: randomized controlled trials; systematic reviews and "
    "meta-analyses;\nclinical practice guidelines and consensus statements; "
    "topics with direct\nbedside impact (analgesia, airway, obstetric "
    "anesthesia, perioperative\nmanagement, regional techniques, patient "
    "safety).\nDEPRIORITIZE: basic-science/animal studies (e.g. mouse models); "
    "bibliometric,\nscientometric, or methodology papers; editorials, "
    "commentaries, and letters."
)


def select_digest_articles(articles: list, per_journal: int = 1,
                           max_total: int = 10) -> list:
    """Pick the best OPEN-ACCESS article per journal, capped at ``max_total``.

    Paywalled articles are excluded entirely. For journals with more than one
    open-access candidate, a single batched Claude call chooses the most
    clinically relevant one across all journals at once.
    """
    # OPEN ACCESS ONLY — never feature paywalled articles.
    oa = [a for a in articles if a.get("is_open_access")]
    dropped = len(articles) - len(oa)
    if dropped:
        logger.info(f"Open-access filter: kept {len(oa)}, dropped {dropped} paywalled")

    by_journal = {}
    for a in oa:
        by_journal.setdefault(a["journal_abbr"], []).append(a)

    selected = []
    # Journals with a single candidate need no API call.
    multi = {abbr: arts for abbr, arts in by_journal.items() if len(arts) > per_journal}
    for abbr, arts in by_journal.items():
        if abbr not in multi:
            selected.extend(arts[:per_journal])

    api_key = _api_key()
    picks = {}
    if api_key and per_journal == 1 and multi:
        picks = _claude_pick_batch(multi, api_key)

    for abbr, arts in multi.items():
        chosen = picks.get(abbr)
        if chosen is None:
            chosen = sorted(arts, key=_heuristic_key, reverse=True)[0]
        selected.append(chosen)

    # Highest-impact journals fill the limited slots first.
    selected.sort(key=lambda a: a["impact_factor"], reverse=True)
    return selected[:max_total]


# ── Claude-driven pick (single batched call across all journals) ─────────────

def _claude_pick_batch(by_journal: dict, api_key: str) -> dict:
    """One API call: choose the best article for EVERY journal at once.

    Returns {journal_abbr: chosen_article}. Missing/failed journals are left
    out so the caller can fall back to the heuristic.
    """
    from config import CLAUDE_MODEL_FAST

    # Build a compact listing; cap candidates per journal and abstract length.
    blocks = []
    capped = {}
    for abbr, arts in by_journal.items():
        cand = arts[:_MAX_CANDIDATES_PER_JOURNAL]
        capped[abbr] = cand
        lines = [f"JOURNAL {abbr}:"]
        for i, art in enumerate(cand, 1):
            ab = (art.get("abstract") or "")[:_ABSTRACT_CHARS]
            lines.append(f"  [{i}] {art['title']}\n      {ab}")
        blocks.append("\n".join(lines))
    listing = "\n\n".join(blocks)

    # A specialty may supply its own editor guidance (role + prioritize/
    # deprioritize) via config.ARTICLE_SELECTOR_PROMPT. When absent, fall back to
    # the built-in anesthesia guidance so anesthesia behaviour is unchanged.
    from config import ARTICLE_SELECTOR_PROMPT as _cfg_sel
    intro = _cfg_sel or _DEFAULT_SELECTOR_INTRO

    prompt = f"""{intro}

CANDIDATES (grouped by journal):
{listing}

Respond with ONLY raw JSON, no markdown — one entry per journal:
{{"picks": [{{"journal": "<abbr>", "choice": <number>}}, ...]}}"""

    try:
        import httpx
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL_FAST,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        text = "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)

        chosen = {}
        for pick in result.get("picks", []):
            abbr = pick.get("journal")
            cand = capped.get(abbr)
            if not cand:
                continue
            idx = int(pick.get("choice", 0)) - 1
            if 0 <= idx < len(cand):
                chosen[abbr] = cand[idx]
        logger.info(f"Batched selection: chose for {len(chosen)}/{len(by_journal)} "
                    f"journals in 1 API call ({CLAUDE_MODEL_FAST})")
        return chosen
    except Exception as e:
        logger.warning(f"Batched article pick failed ({e}); using heuristic")
        return {}


# ── Heuristic fallback ───────────────────────────────────────────────────────

_CLINICAL_KEYWORDS = (
    "randomi", "systematic review", "meta-analysis", "meta analysis",
    "guideline", "consensus", "trial", "analgesi", "airway", "obstetric",
    "perioperative", "regional", "block", "safety", "spinal", "epidural",
    "postoperative", "sedation",
)
_DEPRIORITIZE_KEYWORDS = (
    "mouse", "murine", "rat ", "rodent", "in vitro", "bibliometric",
    "scientometric", "editorial", "letter to", "erratum", "correction",
    "methodolog",
)


def _heuristic_key(art: dict):
    """Score an article without the API: clinical signal, then OA, then date."""
    text = f"{art.get('title', '')} {art.get('abstract', '')}".lower()
    score = sum(1 for k in _CLINICAL_KEYWORDS if k in text)
    score -= sum(2 for k in _DEPRIORITIZE_KEYWORDS if k in text)
    return (score, bool(art.get("is_open_access")), art.get("date") or datetime.min)


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            from config import ANTHROPIC_API_KEY
            key = ANTHROPIC_API_KEY
        except Exception:
            key = ""
    return key


# ── Accessibility-aware scoring + top-N selection ────────────────────────────
# Pick the best N articles OVERALL (not 1-per-journal), preferring those with
# retrievable full text. See Task 3.

def _tier1_abbrs() -> set:
    from config import JOURNALS
    return {j["abbreviation"] for j in JOURNALS if not j.get("filter_required")}


def score_article(art: dict, avail: dict, now: datetime, tier1: set) -> dict:
    """Return {"score": int, "parts": {...}} for one article."""
    parts = {"base": 1}
    if avail.get("has_fulltext"):
        parts["fulltext"] = 3
    if avail.get("is_oa") or art.get("is_open_access"):
        parts["open_access"] = 2
    if art.get("journal_abbr") in tier1:
        parts["tier1"] = 1
    d = art.get("date")
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d)
        except ValueError:
            d = None
    age = (now - d).days if d else 999
    if age <= 7:
        parts["recency"] = 2
    elif age <= 14:
        parts["recency"] = 1
    return {"score": sum(parts.values()), "parts": parts}


def select_top_scored(articles: list, avail_map: dict, sent_keys: set,
                      top_n: int = 5):
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

    def _sort_key(a):
        d = a.get("date")
        if isinstance(d, str):
            try:
                d = datetime.fromisoformat(d)
            except ValueError:
                d = None
        return (a["_score"], a.get("impact_factor", 0) or 0, d or datetime.min)

    ranked.sort(key=_sort_key, reverse=True)
    return ranked[:top_n], ranked
