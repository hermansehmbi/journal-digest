"""
Journal Digest — Feed Verifier
==============================
For the active specialty (SPECIALTY env or --specialty=<x>), this script:

  1. Attempts every journal RSS URL and reports the entry count.
  2. Shows the EFFECTIVE article count from the fetcher (RSS, with the Crossref
     fallback that kicks in when a feed is empty/blocked).
  3. Flags any journal that yields 0 effective articles.
  4. Verifies every podcast feed (per-journal, bonus, and rotation).
  5. For Tier-2 (filter_required) journals, runs the relevance filter on a small
     sample and prints the RELEVANT/total result (skipped if no ANTHROPIC_API_KEY).

Usage:
    python verify_feeds.py --specialty=ep
    SPECIALTY=anesthesia python verify_feeds.py
"""

from __future__ import annotations

import os
import sys

# Resolve --specialty before importing config (config reads SPECIALTY at import).
for _a in sys.argv[1:]:
    if _a.startswith("--specialty="):
        os.environ["SPECIALTY"] = _a.split("=", 1)[1].strip().lower()

import logging
logging.basicConfig(level=logging.WARNING)

import feedparser

import config
from fetcher import fetch_articles, fetch_podcast_episodes, fetch_bonus_podcasts

LOOKBACK = max(config.INITIAL_LOOKBACK_DAYS, 30)


def _raw_entries(url: str) -> tuple[int, str]:
    if not url:
        return 0, "no url"
    try:
        feed = feedparser.parse(url, agent="JournalDigest/1.0 (feed verifier)")
    except Exception as e:
        return 0, f"error: {e}"
    n = len(feed.entries)
    note = ""
    if feed.bozo and not feed.entries:
        note = f"bozo: {str(feed.bozo_exception)[:50]}"
    return n, note


def verify_journals() -> list[dict]:
    print("=" * 78)
    print(f"FEED VERIFICATION — {config.SPECIALTY_NAME} ({config.JOURNAL_COUNT} journals)")
    print("=" * 78)
    print(f"{'Journal':<14}{'RSS raw':>9}{'Effective':>11}  {'Tier':<7}{'OA':<10}Notes")
    print("-" * 78)

    flagged = []
    results = []
    for j in config.JOURNALS:
        abbr = j.get("abbreviation", "?")
        raw, note = _raw_entries(j.get("rss_url"))
        if j.get("rss_url_inpress"):
            raw2, _ = _raw_entries(j["rss_url_inpress"])
            raw += raw2
        try:
            eff = len(fetch_articles(j, since_days=LOOKBACK))
        except Exception as e:
            eff = 0
            note = (note + f" fetch_err: {e}").strip()
        tier = "Tier 2" if j.get("filter_required") else "Tier 1"
        oa = "fully-OA" if j.get("fully_open_access") else "hybrid"
        if not j.get("rss_url") and eff:
            note = (note + " [Crossref fallback]").strip()
        flag = " ⚠ ZERO" if eff == 0 else ""
        print(f"{abbr:<14}{raw:>9}{eff:>11}  {tier:<7}{oa:<10}{note}{flag}")
        if eff == 0:
            flagged.append(abbr)
        results.append({"abbr": abbr, "raw": raw, "eff": eff,
                        "filter_required": bool(j.get("filter_required"))})

    print("-" * 78)
    if flagged:
        print(f"⚠ {len(flagged)} journal(s) returned ZERO articles: {', '.join(flagged)}")
    else:
        print("✓ Every journal returned at least one article.")
    return results


def verify_podcasts():
    print()
    print("=" * 78)
    print("PODCAST FEEDS")
    print("=" * 78)
    # Per-journal podcasts
    for j in config.JOURNALS:
        pod = j.get("podcast")
        if pod and pod.get("rss_url"):
            eps = fetch_podcast_episodes(j, max_episodes=1)
            _pod_line(pod["name"], eps)
    # Bonus
    for pod in config.BONUS_PODCASTS:
        eps = fetch_bonus_podcasts([pod], max_episodes=1)
        _pod_line(pod["name"] + " (bonus)", eps)
    # Rotation
    for group in config.PODCAST_ROTATION:
        for pod in group.get("podcasts", []):
            if not pod.get("rss_url") or "FIND_THE_RSS" in str(pod.get("rss_url")):
                print(f"  ⚠ {pod.get('name','?'):<34} MISSING rss_url")
                continue
            eps = fetch_bonus_podcasts(
                [{"name": pod["name"], "rss_url": pod["rss_url"],
                  "website": pod.get("url", "")}], max_episodes=1)
            _pod_line(pod["name"] + f" (rotation {group.get('week_mod')})", eps)


def _pod_line(name: str, eps: list):
    if eps:
        e = eps[0]
        print(f"  ✓ {name:<40} latest: {e.get('date_str','?')} — "
              f"{(e.get('title') or '')[:46]}")
    else:
        print(f"  ⚠ {name:<40} NO EPISODES")


def verify_relevance_sample():
    tier2 = [j for j in config.JOURNALS if j.get("filter_required")]
    if not tier2:
        return
    print()
    print("=" * 78)
    print("RELEVANCE FILTER SAMPLE (Tier-2 / general journals)")
    print("=" * 78)
    if not (os.environ.get("ANTHROPIC_API_KEY") or config.ANTHROPIC_API_KEY):
        print("  (skipped — no ANTHROPIC_API_KEY set)")
        return
    import relevance_filter
    for j in tier2:
        arts = fetch_articles(j, since_days=LOOKBACK)[:6]
        if not arts:
            print(f"  {j['abbreviation']}: no articles to sample")
            continue
        kept = relevance_filter.filter_articles(arts)
        print(f"  {j['abbreviation']}: {len(kept)}/{len(arts)} sampled articles RELEVANT")
        for a in arts:
            mark = "✓" if a in kept else "·"
            print(f"     {mark} {(a.get('title') or '')[:68]}")


if __name__ == "__main__":
    verify_journals()
    verify_podcasts()
    verify_relevance_sample()
    print("\nDone.")
