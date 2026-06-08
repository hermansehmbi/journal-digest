"""
Journal Digest — Main (multi-specialty)
=======================================
Usage:
    python main.py digest          # Monday weekly article digest
    python main.py saturday        # Saturday CME + week highlights
    python main.py monthly         # Monthly top-5 + MOC tracker
    python main.py preview         # Preview digest locally (no email)
    python main.py preview-sat     # Preview Saturday email locally
    python main.py preview-month   # Preview monthly email locally

Pick the specialty with --specialty=ep / --specialty=anesthesia (or the
SPECIALTY env var). Defaults to "anesthesia".
    python main.py preview --specialty=ep
    SPECIALTY=ep python main.py digest
"""

from __future__ import annotations

import os
import sys

# ── Resolve the specialty BEFORE importing config ────────────────────────────
# config.py reads SPECIALTY at import time, so a --specialty=<x> CLI flag must be
# promoted to the environment before any `from config import ...` runs.


def _extract_specialty(argv: list[str]) -> list[str]:
    """Pop a --specialty=<x> / --specialty <x> flag from argv, set SPECIALTY env,
    and return the remaining argv (so the positional command still works)."""
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--specialty="):
            os.environ["SPECIALTY"] = a.split("=", 1)[1].strip().lower()
        elif a == "--specialty" and i + 1 < len(argv):
            os.environ["SPECIALTY"] = argv[i + 1].strip().lower()
            i += 1
        else:
            rest.append(a)
        i += 1
    return rest


_ARGS = _extract_specialty(sys.argv[1:])

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    JOURNALS, BONUS_PODCASTS, RECIPIENT_EMAIL, SENDER_EMAIL,
    MODE, INITIAL_LOOKBACK_DAYS, SPECIALTY, SPECIALTY_NAME,
    AUDIO_ENABLED, DEEPDIVE_ENABLED, PUBLISH_ENABLED, AUDIO_DELIVERY,
    cache_path,
)
from fetcher import fetch_articles, fetch_podcast_episodes, fetch_bonus_podcasts
import article_selector
from email_builder import build_digest_email, build_saturday_email, build_monthly_email
from email_sender import send_email
from moc_tracker import log_articles, log_cme, update_summary, MOC_FILE
import relevance_filter
import gsheets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Regenerable article cache (under .cache/<specialty>/). The Saturday hand-off
# files (monday_featured/weekly_summaries/sent) stay at the repo root.
CACHE_FILE = cache_path("articles_cache.json")
# The EXACT final list of articles emailed in the most recent Monday digest.
# Saturday CME reads ONLY from this file — no re-fetching, no new articles.
MONDAY_FEATURED_FILE = f"monday_featured_{SPECIALTY}.json"
# Monday's Deep Dive summaries + the published page URL. Saturday reuses these to
# ground the CME questions (Option B) and to link the same page in the CME email.
WEEKLY_SUMMARIES_FILE = f"weekly_summaries_{SPECIALTY}.json"
# DOIs/keys of articles already emailed in any prior digest — never repeat one.
SENT_FILE = f"sent_articles_{SPECIALTY}.json"


def _fetch_all_articles(since_days: int) -> list:
    """Fetch every journal, applying the relevance filter to Tier-2 (general)
    journals flagged filter_required so off-topic articles are dropped before
    selection. Tier-1 dedicated journals are never filtered."""
    out = []
    for j in JOURNALS:
        arts = fetch_articles(j, since_days=since_days)
        if j.get("filter_required") and arts:
            arts = relevance_filter.filter_articles(arts)
        out.extend(arts)
    return out


def _digest_since() -> int:
    """Look-back window for the digest (the scored selection's multi-week window)."""
    from config import SELECTION_LOOKBACK_DAYS
    return SELECTION_LOOKBACK_DAYS


def _select_digest(all_articles: list) -> list:
    """Pick the featured articles (scored selection lives in article_selector)."""
    return article_selector.select_for_digest(all_articles, _load_sent_keys())


def run_digest(preview=False):
    """Monday (weekly): articles + podcasts + audio (if API mode & enabled)."""
    logger.info("=" * 50)
    logger.info(f"DIGEST [{SPECIALTY}] — fetching articles and podcasts")
    logger.info("=" * 50)

    all_articles = _fetch_all_articles(_digest_since())

    all_pods = []
    for j in JOURNALS:
        all_pods.extend(fetch_podcast_episodes(j, max_episodes=1))
    bonus = fetch_bonus_podcasts(BONUS_PODCASTS, max_episodes=1)
    # Rotating specialty podcasts (embedded links, not generated audio).
    rotation = _rotation_podcasts()

    logger.info(f"Total: {len(all_articles)} articles, "
                f"{len(all_pods) + len(rotation)} podcast eps")

    # Cache the full set first so Saturday's weekly review sees everything.
    _cache_articles(all_articles)

    # Pick the articles to feature (per the specialty's selection_mode).
    selected = _select_digest(all_articles)
    logger.info(f"Selected {len(selected)} articles for the digest email")

    # Save the EXACT final list emailed in this Monday digest (overwrite). The
    # Saturday CME reads only from this so it tests precisely these articles.
    _write_monday_featured(selected)

    # Rolling MOC log: append these articles (Google Sheets, or xlsx fallback).
    if not preview:
        _track_digest(selected)

    # API mode: generate the two-host audio podcast from the same selection so
    # the audio summary matches the articles shown in the email, then publish
    # it to GitHub Pages for inline playback. Skipped if the specialty disables
    # audio (e.g. EP launches without TTS — the email works with links alone).
    has_audio = False
    podcast_url = None
    audio_attached = False
    audio_path = None
    if MODE == "api" and AUDIO_ENABLED and selected:
        try:
            from podcast_generator import generate_podcast
            audio_name = (f"{SPECIALTY_NAME.replace(' ', '_')}_Digest_"
                          f"{datetime.now().strftime('%Y_%m_%d')}.mp3")
            audio_file = generate_podcast(selected, output_path=audio_name)
            if audio_file:
                audio_path = audio_file
                if AUDIO_DELIVERY == "attach":
                    # Deliver the MP3 as an email attachment (no hosted player).
                    has_audio = True
                    audio_attached = True
                elif PUBLISH_ENABLED:
                    from publisher import publish_episode
                    podcast_url = publish_episode(audio_file, datetime.now(),
                                                  push=not preview)
                    has_audio = podcast_url is not None
        except Exception as e:
            logger.error(f"Podcast generation/publishing failed: {e}")

    # API mode: generate the structured "Deep Dive" summaries for each featured
    # article and (optionally) publish them. Skipped if disabled for the
    # specialty; Saturday CME then grounds on cached full text instead.
    summaries = None
    deepdive_url = None
    if MODE == "api" and DEEPDIVE_ENABLED and selected:
        try:
            from summary_generator import generate_summaries
            summaries = generate_summaries(selected)
        except Exception as e:
            logger.error(f"Deep Dive summary generation failed: {e}")
        if summaries and PUBLISH_ENABLED:
            try:
                from publisher import publish_deepdive
                deepdive_url = publish_deepdive(summaries, datetime.now(),
                                                push=not preview)
            except Exception as e:
                logger.error(f"Deep Dive publishing failed: {e}")
    if not preview:
        _write_weekly_summaries(summaries, deepdive_url)

    # Build and send. The rotation podcasts are appended to the bonus list so
    # they render in the same "more podcasts" section of the email.
    subject, html = build_digest_email(selected, all_pods, bonus + rotation,
                                       has_audio=has_audio,
                                       podcast_url=podcast_url,
                                       deepdive_url=deepdive_url,
                                       summaries=summaries,
                                       audio_attached=audio_attached)

    if preview:
        _save(subject, html, "digest")
    else:
        # In "attach" mode the MP3 rides along as an email attachment.
        attachments = [audio_path] if (audio_attached and audio_path) else []
        send_email(RECIPIENT_EMAIL, subject, html, SENDER_EMAIL,
                   attachments=attachments)
        # Record what we sent so scored selection never repeats it.
        from config import SELECTION_MODE
        if SELECTION_MODE == "scored":
            _add_sent_keys(selected)


def run_saturday(preview=False):
    """Saturday: CME questions built from EXACTLY this week's Monday digest."""
    logger.info("=" * 50)
    logger.info(f"SATURDAY [{SPECIALTY}] — CME and weekly review")
    logger.info("=" * 50)

    # Locked to Monday's digest: read ONLY the exact articles that were emailed
    # Monday. No re-fetching, no newly-appeared articles.
    week = _load_monday_featured()
    logger.info(f"Monday digest articles: {len(week)} "
                f"(locked to {MONDAY_FEATURED_FILE})")

    # Monday's Deep Dive summaries + page URL (for CME grounding and the email link).
    wk = _load_weekly_summaries()
    summaries = wk.get("summaries") or None
    deepdive_url = wk.get("deepdive_url") or None

    # Free mode has no CME — just a weekly highlights review of Monday's set.
    if MODE != "api":
        subject, html = build_saturday_email(week, cme_questions=None, quiz_url=None,
                                             deepdive_url=deepdive_url)
        if preview:
            _save(subject, html, "saturday")
        else:
            send_email(RECIPIENT_EMAIL, subject, html, SENDER_EMAIL)
        return

    # API mode: CME is required. Never send a question-less email — if anything
    # fails, flag it (non-zero exit) so the failure is visible instead of silent.
    if not week:
        logger.error(f"No Monday digest articles in {MONDAY_FEATURED_FILE} — "
                     "run the Monday digest first. NOT sending a CME email.")
        if not preview:
            sys.exit(1)
        return

    cme = None
    try:
        from cme_generator import generate_cme_questions
        cme = generate_cme_questions(week, num_questions=_cme_count(week),
                                     summaries=summaries)
    except Exception as e:
        logger.error(f"CME generation raised: {e}")

    if not cme:
        logger.error("CME generation failed (no questions produced) — "
                     "NOT sending a question-less email.")
        if not preview:
            sys.exit(1)
        return

    if not preview:
        _track_cme(cme)

    quiz_url = None
    if PUBLISH_ENABLED:
        try:
            from publisher import publish_cme_quiz
            quiz_url = publish_cme_quiz(cme, datetime.now(), push=not preview)
        except Exception as e:
            logger.error(f"CME quiz publishing failed: {e}")

    subject, html = build_saturday_email(week, cme_questions=cme, quiz_url=quiz_url,
                                         deepdive_url=deepdive_url)
    if preview:
        _save(subject, html, "saturday")
    else:
        send_email(RECIPIENT_EMAIL, subject, html, SENDER_EMAIL)


def run_monthly(preview=False):
    """1st of month: top 5 articles + MOC tracker refresh."""
    logger.info("=" * 50)
    logger.info(f"MONTHLY [{SPECIALTY}] — digest and MOC update")
    logger.info("=" * 50)

    all_articles = _fetch_all_articles(30)
    logger.info(f"Month's articles: {len(all_articles)}")

    # Prioritize: open access first, then by impact factor
    oa = sorted([a for a in all_articles if a["is_open_access"]],
                key=lambda a: a["impact_factor"], reverse=True)
    rest = sorted([a for a in all_articles if not a["is_open_access"]],
                  key=lambda a: a["impact_factor"], reverse=True)
    ranked = oa + rest
    top5 = ranked[:5]

    # MOC: live Google Sheet (Monthly summary) or local xlsx fallback.
    used_google = _track_monthly(ranked[:20])

    subject, html = build_monthly_email(all_articles, top5)

    # Attach the local xlsx only in fallback mode (Google sheet is the live copy).
    attachments = [] if used_google else [MOC_FILE]
    if preview:
        _save(subject, html, "monthly")
    else:
        send_email(RECIPIENT_EMAIL, subject, html, SENDER_EMAIL,
                   attachments=attachments)


def _load_sent_keys() -> set:
    """Keys (doi:… / url:…) of articles emailed in prior digests."""
    p = Path(SENT_FILE)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else data.get("keys", []))
    except Exception:
        return set()


def _add_sent_keys(articles: list):
    """Record the just-sent articles so future digests never repeat them."""
    import fulltext_resolver as fr
    keys = _load_sent_keys()
    for a in articles:
        keys.add(fr.key(a))
    Path(SENT_FILE).write_text(json.dumps(sorted(keys)), encoding="utf-8")
    logger.info(f"Recorded {len(articles)} sent articles → {SENT_FILE} "
                f"({len(keys)} total)")


def run_scan():
    """Dry run (Task 6): fetch 4 weeks, check full-text availability (Unpaywall +
    PMC), relevance-filter Tier 2, score, and print a table. NO Claude summaries
    (only Haiku for Tier-2 filtering) — cheap, to judge the full-text strategy."""
    import fulltext_resolver as fr
    from article_selector import select_top_scored
    SINCE = _digest_since()
    logger.info("=" * 50)
    logger.info(f"SCAN [{SPECIALTY}] — {SINCE}-day window, no summaries")
    logger.info("=" * 50)

    per_journal = {}
    all_articles = []
    for j in JOURNALS:
        arts = fetch_articles(j, since_days=SINCE)
        if j.get("filter_required") and arts:
            arts = relevance_filter.filter_articles(arts)
        per_journal[j["abbreviation"]] = {"total": len(arts), "ft": 0, "abs": 0,
                                          "nodoi": 0}
        all_articles.extend(arts)

    # Dedup by key.
    seen, uniq = set(), []
    for a in all_articles:
        k = fr.key(a)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(a)

    logger.info(f"{len(uniq)} unique articles; resolving missing DOIs (Crossref) …")
    fr.resolve_dois(uniq)
    logger.info("Checking full-text availability (Unpaywall + PMC) …")
    avail = fr.availability_map(uniq)
    sent = _load_sent_keys()
    from config import DIGEST_TOP_N, MAX_PER_JOURNAL
    top, ranked = select_top_scored(uniq, avail, sent, top_n=max(10, DIGEST_TOP_N),
                                    max_per_journal=(MAX_PER_JOURNAL or None))

    # Tally per journal.
    with_doi = ft_total = unpaywall_hits = 0
    for a in uniq:
        av = avail.get(fr.key(a), {})
        pj = per_journal.get(a.get("journal_abbr"))
        if av.get("has_doi"):
            with_doi += 1
        else:
            if pj:
                pj["nodoi"] += 1
        if av.get("has_fulltext"):
            ft_total += 1
            if pj:
                pj["ft"] += 1
            if str(av.get("via", "")).startswith("unpaywall"):
                unpaywall_hits += 1
        else:
            if pj:
                pj["abs"] += 1

    print("\n================= PER-JOURNAL =================")
    print(f"{'Journal':<12}{'total':>6}{'fulltext':>10}{'abs-only':>10}{'no-DOI':>8}")
    for abbr, c in per_journal.items():
        print(f"{abbr:<12}{c['total']:>6}{c['ft']:>10}{c['abs']:>10}{c['nodoi']:>8}")

    print("\n================= TOP 10 SCORED =================")
    print(f"{'#':>2} {'score':>5} {'jrnl':<11} {'full text?':<22} {'sent?':<6} title")
    for i, a in enumerate(top, 1):
        av = a.get("_avail", {})
        ftxt = (av.get("via") or "abstract-only") if av.get("has_fulltext") else "abstract-only"
        parts = "+".join(f"{k}:{v}" for k, v in a.get("_score_parts", {}).items())
        was_sent = "SENT" if fr.key(a) in sent else ""
        print(f"{i:>2} {a['_score']:>5} {a.get('journal_abbr',''):<11} "
              f"{ftxt:<22} {was_sent:<6} {a.get('title','')[:60]}")
        print(f"       ({parts})")

    print("\n================= SUMMARY =================")
    print(f"Articles (unique, {SINCE}d):     {len(uniq)}")
    print(f"  with a DOI:                {with_doi}")
    print(f"  full text available:       {ft_total}  "
          f"({100*ft_total//max(1,len(uniq))}% of all, "
          f"{100*ft_total//max(1,with_doi)}% of DOI'd)")
    print(f"  abstract-only:             {len(uniq)-ft_total}")
    print(f"Unpaywall hit rate:          {unpaywall_hits}/{with_doi} with-DOI "
          f"({100*unpaywall_hits//max(1,with_doi)}%)")
    print(f"Already-sent (excluded):     {sum(1 for a in uniq if fr.key(a) in sent)}")
    print(f"Top-{DIGEST_TOP_N} picks full-text:       "
          f"{sum(1 for a in top[:DIGEST_TOP_N] if a.get('_avail',{}).get('has_fulltext'))}"
          f"/{DIGEST_TOP_N}")


def run_sample_quiz():
    """Write a standalone sample interactive quiz page to docs/<cme-subdir>/
    sample.html for local design preview (no API, no email). Uses the active
    specialty's accent + branding via the shared brandize pass."""
    from pathlib import Path as _P
    from config import DOCS_DIR, DOCS_CME_SUBDIR
    from cme_quiz import build_quiz_page
    from branding import brandize
    sample = [
        {"question": "A 58-year-old with drug-refractory paroxysmal AF is scheduled "
         "for a first ablation. Which energy modality has level-1 evidence for "
         "comparable efficacy with a favourable safety profile for pulmonary vein "
         "isolation?", "options": {"A": "Pulsed field ablation (PFA)",
         "B": "Surgical maze only", "C": "Empirical amiodarone", "D": "AV node ablation"},
         "correct": "A", "rationale": "Sample item for design preview only.",
         "source_article": "Sample article — PVI energy sources",
         "source_journal": "Sample", "source_url": "https://example.org"},
        {"question": "During a VT ablation the patient develops sudden hypotension "
         "and a pericardial effusion on ICE. What is the best immediate next step?",
         "options": {"A": "Continue mapping", "B": "Pericardiocentesis and reverse "
         "anticoagulation", "C": "Increase sedation", "D": "Rapid atrial pacing"},
         "correct": "B", "rationale": "Sample item for design preview only.",
         "source_article": "Sample article — EP complications",
         "source_journal": "Sample", "source_url": "https://example.org"},
        {"question": "A CRT-D patient presents with recurrent inappropriate shocks "
         "from T-wave oversensing. Which programming change is most appropriate?",
         "options": {"A": "Disable all therapy", "B": "Lower the sensitivity / adjust "
         "decay delay", "C": "Increase shock energy", "D": "Shorten the VT detection "
         "interval"}, "correct": "B", "rationale": "Sample item for design preview only.",
         "source_article": "Sample article — device programming",
         "source_journal": "Sample", "source_url": "https://example.org"},
    ]
    docs = _P(DOCS_DIR) / DOCS_CME_SUBDIR
    docs.mkdir(parents=True, exist_ok=True)
    out = docs / "sample.html"
    out.write_text(brandize(build_quiz_page(sample, datetime.now())), encoding="utf-8")
    logger.info(f"Wrote sample quiz page → {out}")
    print(str(out))


def run_preview_deepdive():
    """Build ONLY the Deep Dive page as a standalone local HTML file for review —
    no audio, no email, no publish/push. Uses the active specialty's prompt +
    branding, so the file is self-contained and safe to forward to a colleague."""
    logger.info("=" * 50)
    logger.info(f"DEEP DIVE PREVIEW [{SPECIALTY}] — HTML only, no email/push")
    logger.info("=" * 50)
    if MODE != "api":
        logger.error("Deep Dive needs MODE=api and ANTHROPIC_API_KEY.")
        sys.exit(1)
    all_articles = _fetch_all_articles(_digest_since())
    selected = _select_digest(all_articles)
    logger.info(f"Selected {len(selected)} articles for the Deep Dive preview")
    if not selected:
        logger.error("No open-access articles selected — nothing to summarize.")
        sys.exit(1)
    from summary_generator import generate_summaries
    summaries = generate_summaries(selected)
    if not summaries:
        logger.error("No Deep Dive summaries generated (check ANTHROPIC_API_KEY).")
        sys.exit(1)
    from deepdive_builder import build_deepdive_page
    from branding import brandize
    html = brandize(build_deepdive_page(summaries, datetime.now()))
    f = f"preview_{SPECIALTY}_deepdive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    Path(f).write_text(html, encoding="utf-8")
    logger.info(f"Deep Dive preview: {f} ({len(summaries)} articles)")
    print(f)


# ── Specialty helpers ────────────────────────────────────────────────────────

def _cme_count(week: list) -> int:
    """How many CME questions to generate: a fixed int from config, or one per
    article when the specialty uses 'per_article'."""
    from config import CME_NUM_QUESTIONS
    if isinstance(CME_NUM_QUESTIONS, int):
        return min(CME_NUM_QUESTIONS, len(week)) if week else CME_NUM_QUESTIONS
    return len(week)


def _rotation_podcasts() -> list:
    """Resolve this week's embedded podcast links from PODCAST_ROTATION (rotated
    by ISO week number), fetching each feed's latest episode. Returns [] if the
    specialty defines no rotation (e.g. anesthesia uses per-journal podcasts)."""
    from config import PODCAST_ROTATION
    if not PODCAST_ROTATION:
        return []
    week_num = datetime.now().isocalendar().week
    chosen = None
    for group in PODCAST_ROTATION:
        mods = group.get("week_mod") or []
        if (week_num % 4) in mods:
            chosen = group
            break
    if chosen is None and PODCAST_ROTATION:
        chosen = PODCAST_ROTATION[week_num % len(PODCAST_ROTATION)]

    pods = [{"name": p["name"], "rss_url": p.get("rss_url"),
             "website": p.get("url", ""), "description": p.get("description", "")}
            for p in (chosen.get("podcasts") if chosen else [])
            if p.get("rss_url") and "FIND_THE_RSS" not in str(p.get("rss_url"))]
    if not pods:
        return []
    return fetch_bonus_podcasts(pods, max_episodes=1)


# ── MOC tracking (Google Sheets, with local xlsx fallback) ───────────────────

def _track_digest(selected: list):
    """Append featured articles to the live MOC sheet, or the xlsx fallback."""
    today = datetime.now().strftime("%Y-%m-%d")
    if gsheets.is_configured():
        try:
            url = gsheets.log_digest(selected, today)
            logger.info(f"MOC mode: Google Sheets (live) → {url}")
            return
        except Exception as e:
            logger.error(f"Google Sheets MOC failed ({e}); falling back to local xlsx")
    else:
        logger.info("MOC mode: local xlsx (Google not configured)")
    try:
        log_articles(selected)
    except Exception as e:
        logger.error(f"Local xlsx MOC fallback failed: {e}")


def _track_cme(cme: list):
    """Append a CME Log row to the live MOC sheet, or the xlsx fallback."""
    today = datetime.now().strftime("%Y-%m-%d")
    if gsheets.is_configured():
        try:
            url = gsheets.log_cme(today, len(cme))
            logger.info(f"MOC mode: Google Sheets (live) → {url}")
            return
        except Exception as e:
            logger.error(f"Google Sheets CME log failed ({e}); falling back to local xlsx")
    else:
        logger.info("MOC mode: local xlsx (Google not configured)")
    try:
        log_cme(cme)
    except Exception as e:
        logger.error(f"Local xlsx CME fallback failed: {e}")


def _track_monthly(fallback_articles: list) -> bool:
    """Refresh the live Summary & Report in Google Sheets. Returns True if the
    Google path was used; False means the xlsx fallback ran (attach it)."""
    if gsheets.is_configured():
        try:
            url = gsheets.refresh_summary()
            logger.info(f"MOC mode: Google Sheets (live) → {url}")
            return True
        except Exception as e:
            logger.error(f"Google Sheets monthly update failed ({e}); falling back to xlsx")
    else:
        logger.info("MOC mode: local xlsx (Google not configured)")
    try:
        log_articles(fallback_articles)
        update_summary()
    except Exception as e:
        logger.error(f"Local xlsx monthly fallback failed: {e}")
    return False


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_articles(articles: list):
    cache = _load_cache_raw()
    seen = {a["url"] for a in cache}
    for art in articles:
        if art["url"] not in seen:
            copy = dict(art)
            if isinstance(copy.get("date"), datetime):
                copy["date"] = copy["date"].isoformat()
            cache.append(copy)
    Path(CACHE_FILE).write_text(json.dumps(cache, default=str), encoding="utf-8")


def _load_cache_raw() -> list:
    p = Path(CACHE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _load_cached(days=7) -> list:
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for art in _load_cache_raw():
        try:
            d = datetime.fromisoformat(art["date"]) if art.get("date") else None
            if d and d >= cutoff:
                out.append(art)
        except (ValueError, TypeError):
            out.append(art)
    return out


def _write_monday_featured(selected: list):
    """Overwrite monday_featured_<specialty>.json with the EXACT list emailed."""
    out = []
    for art in selected:
        copy = dict(art)
        if isinstance(copy.get("date"), datetime):
            copy["date"] = copy["date"].isoformat()
        out.append(copy)
    Path(MONDAY_FEATURED_FILE).write_text(json.dumps(out, default=str),
                                          encoding="utf-8")
    logger.info(f"Saved {len(out)} Monday featured articles → {MONDAY_FEATURED_FILE}")


def _load_monday_featured() -> list:
    """Load the exact Monday digest article list (or [] if none yet)."""
    p = Path(MONDAY_FEATURED_FILE)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_weekly_summaries(summaries: list | None, deepdive_url: str | None):
    """Persist Monday's Deep Dive summaries + page URL for Saturday to reuse."""
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "deepdive_url": deepdive_url,
        "summaries": summaries or [],
    }
    Path(WEEKLY_SUMMARIES_FILE).write_text(json.dumps(payload, default=str),
                                           encoding="utf-8")
    logger.info(f"Saved {len(summaries or [])} Deep Dive summaries → "
                f"{WEEKLY_SUMMARIES_FILE}")


def _load_weekly_summaries() -> dict:
    """Load Monday's Deep Dive summaries payload (or {} if none yet)."""
    p = Path(WEEKLY_SUMMARIES_FILE)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(subject, html, label):
    f = f"preview_{SPECIALTY}_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    Path(f).write_text(html, encoding="utf-8")
    logger.info(f"Preview: {f} (Subject: {subject})")


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not _ARGS:
        print(__doc__)
        sys.exit(1)

    cmd = _ARGS[0].lower()
    cmds = {
        "digest": lambda: run_digest(preview=False),
        "saturday": lambda: run_saturday(preview=False),
        "monthly": lambda: run_monthly(preview=False),
        "preview": lambda: run_digest(preview=True),
        "preview-sat": lambda: run_saturday(preview=True),
        "preview-month": lambda: run_monthly(preview=True),
        "sample-quiz": run_sample_quiz,
        "preview-deepdive": run_preview_deepdive,
        "scan": run_scan,
    }

    if cmd in cmds:
        cmds[cmd]()
    else:
        print(f"Unknown: {cmd}\n{__doc__}")
        sys.exit(1)
