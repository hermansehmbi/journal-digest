"""
Anesthesia Journal Digest — RSS & Podcast Fetcher
"""

import re
import feedparser
import httpx
import logging
from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

USER_AGENT = "AnesthesiaDigest/1.0 (Academic RSS aggregator)"

# Crossref "polite pool" identifies us by mailto for better reliability.
try:
    from config import RECIPIENT_EMAIL as _CR_MAILTO
except Exception:
    _CR_MAILTO = "anesthesia-digest@example.com"
CROSSREF_UA = f"AnesthesiaDigest/1.0 (mailto:{_CR_MAILTO})"


def fetch_articles(journal: dict, since_days: int = 4) -> list[dict]:
    """Fetch recent articles from a journal's RSS feed."""
    # Journals flagged fully_open_access publish every article under an open
    # licence, so every article counts as open access regardless of what the
    # feed / Crossref metadata happens to expose for an individual item.
    fully_oa = bool(journal.get("fully_open_access"))
    articles = []
    for url_key in ["rss_url", "rss_url_inpress"]:
        rss_url = journal.get(url_key)
        if not rss_url:
            continue

        logger.info(f"Fetching: {journal['abbreviation']} ({url_key})")
        try:
            feed = feedparser.parse(rss_url, agent=USER_AGENT)
        except Exception as e:
            logger.error(f"  Failed: {e}")
            continue

        if feed.bozo and not feed.entries:
            logger.warning(f"  Feed error: {feed.bozo_exception}")
            continue

        cutoff = datetime.now() - timedelta(days=since_days)
        seen_urls = {a["url"] for a in articles}

        for entry in feed.entries:
            pub_date = _parse_date(entry)
            if pub_date and pub_date < cutoff:
                continue
            url = entry.get("link", "")
            if url in seen_urls:
                continue

            articles.append({
                "title": _clean(entry.get("title", "Untitled")),
                "authors": _authors(entry),
                "url": url,
                "doi": _doi(entry),
                "date": pub_date,
                "date_str": pub_date.strftime("%Y-%m-%d") if pub_date else "Recent",
                "abstract": _clean(entry.get("summary", "")),
                "is_open_access": fully_oa or _is_oa(entry, journal["publisher"]),
                "journal": journal["name"],
                "journal_abbr": journal["abbreviation"],
                "impact_factor": journal["impact_factor"],
            })
            seen_urls.add(url)

    # Many publisher RSS feeds (LWW, ScienceDirect, Springer) sit behind
    # Cloudflare / anti-bot blocks and return 0 entries. Fall back to the
    # Crossref API, which is a clean JSON endpoint keyed by ISSN.
    if not articles and journal.get("issn"):
        logger.info(f"  RSS empty for {journal['abbreviation']}; trying Crossref")
        articles = _fetch_crossref(journal, since_days)

    logger.info(f"  → {len(articles)} articles from {journal['abbreviation']}")
    return articles


def _fetch_crossref(journal: dict, since_days: int) -> list[dict]:
    """Fetch recent articles from the Crossref REST API by ISSN."""
    fully_oa = bool(journal.get("fully_open_access"))
    cutoff = datetime.now() - timedelta(days=since_days)
    params = {
        "filter": f"from-pub-date:{cutoff.strftime('%Y-%m-%d')}",
        "sort": "published",
        "order": "desc",
        "rows": 60,
        "select": "title,DOI,author,published,URL,abstract,license",
    }
    url = f"https://api.crossref.org/journals/{journal['issn']}/works"
    try:
        resp = httpx.get(url, params=params,
                         headers={"User-Agent": CROSSREF_UA}, timeout=30)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as e:
        logger.warning(f"  Crossref failed for {journal['abbreviation']}: {e}")
        return []

    articles = []
    seen_urls = set()
    for item in items:
        pub_date = _crossref_date(item)
        if pub_date and pub_date < cutoff:
            continue

        doi = item.get("DOI", "")
        link = item.get("URL", "") or (f"https://doi.org/{doi}" if doi else "")
        if not link or link in seen_urls:
            continue

        title_list = item.get("title") or ["Untitled"]
        articles.append({
            "title": _clean(title_list[0]),
            "authors": _crossref_authors(item),
            "url": link,
            "doi": doi,
            "date": pub_date,
            "date_str": pub_date.strftime("%Y-%m-%d") if pub_date else "Recent",
            "abstract": _clean(item.get("abstract", "")),
            "is_open_access": fully_oa or _crossref_is_oa(item),
            "journal": journal["name"],
            "journal_abbr": journal["abbreviation"],
            "impact_factor": journal["impact_factor"],
        })
        seen_urls.add(link)

    return articles


def fetch_podcast_episodes(journal: dict, max_episodes: int = 1) -> list[dict]:
    """Fetch latest podcast episodes for a journal."""
    podcast = journal.get("podcast")
    if not podcast or not podcast.get("rss_url"):
        return []

    logger.info(f"Podcast: {podcast['name']}")
    try:
        feed = feedparser.parse(podcast["rss_url"], agent=USER_AGENT)
    except Exception as e:
        logger.error(f"  Failed: {e}")
        return []

    episodes = []
    for entry in feed.entries[:max_episodes]:
        audio_url = ""
        for link in entry.get("enclosures", []) + entry.get("links", []):
            if "audio" in link.get("type", ""):
                audio_url = link.get("href", "")
                break

        pub = _parse_date(entry)
        episodes.append({
            "title": _clean(entry.get("title", "Untitled")),
            "url": entry.get("link", audio_url),
            "audio_url": audio_url,
            "date_str": pub.strftime("%Y-%m-%d") if pub else "Recent",
            "duration": entry.get("itunes_duration", ""),
            "description": _clean(entry.get("summary", ""))[:250],
            "podcast_name": podcast["name"],
            "journal_abbr": journal["abbreviation"],
            "website": podcast.get("website", ""),
        })
    return episodes


def fetch_bonus_podcasts(bonus_list: list, max_episodes: int = 1) -> list[dict]:
    """Fetch episodes from non-journal podcasts like ASA Central Line."""
    episodes = []
    for pod in bonus_list:
        if not pod.get("rss_url"):
            continue
        try:
            feed = feedparser.parse(pod["rss_url"], agent=USER_AGENT)
            for entry in feed.entries[:max_episodes]:
                audio_url = ""
                for link in entry.get("enclosures", []) + entry.get("links", []):
                    if "audio" in link.get("type", ""):
                        audio_url = link.get("href", "")
                        break
                pub = _parse_date(entry)
                episodes.append({
                    "title": _clean(entry.get("title", "Untitled")),
                    "url": entry.get("link", audio_url),
                    "audio_url": audio_url,
                    "date_str": pub.strftime("%Y-%m-%d") if pub else "Recent",
                    "duration": entry.get("itunes_duration", ""),
                    "description": _clean(entry.get("summary", ""))[:250],
                    "podcast_name": pod["name"],
                    "journal_abbr": "",
                    "website": pod.get("website", ""),
                })
        except Exception as e:
            logger.error(f"Bonus podcast {pod['name']} failed: {e}")
    return episodes


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_date(entry) -> Optional[datetime]:
    for field in ["published_parsed", "updated_parsed", "created_parsed"]:
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6])
            except (TypeError, ValueError):
                continue
    for field in ["published", "updated", "dc_date"]:
        s = entry.get(field, "")
        if s:
            for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
                        "%a, %d %b %Y %H:%M:%S GMT"]:
                try:
                    return datetime.strptime(s.strip(), fmt)
                except ValueError:
                    continue
    return None


def _crossref_date(item) -> Optional[datetime]:
    for field in ["published", "published-online", "published-print", "issued"]:
        parts = (item.get(field) or {}).get("date-parts", [])
        if parts and parts[0] and parts[0][0]:
            y = parts[0][0]
            m = parts[0][1] if len(parts[0]) > 1 else 1
            d = parts[0][2] if len(parts[0]) > 2 else 1
            try:
                return datetime(y, m, d)
            except (TypeError, ValueError):
                continue
    return None


def _crossref_authors(item) -> str:
    names = []
    for a in item.get("author", []):
        full = " ".join(p for p in [a.get("given", ""), a.get("family", "")] if p)
        if full:
            names.append(full)
    if not names:
        return ""
    return "; ".join(names[:4]) + (" et al." if len(names) > 4 else "")


def _crossref_is_oa(item) -> bool:
    for lic in item.get("license", []):
        if "creativecommons.org" in str(lic.get("URL", "")).lower():
            return True
    return False


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    for old, new in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&#39;", "'")]:
        text = text.replace(old, new)
    return text


def _authors(entry) -> str:
    authors = entry.get("authors", [])
    if authors:
        names = [a.get("name", "") for a in authors if a.get("name")]
        if names:
            return "; ".join(names[:4]) + (" et al." if len(names) > 4 else "")
    return entry.get("author", "") or entry.get("dc_creator", "")


def _doi(entry) -> str:
    for field in ["prism_doi", "dc_identifier"]:
        val = entry.get(field, "")
        if val and "10." in val:
            return val
    for field in ["link", "id"]:
        m = re.search(r"(10\.\d{4,}/[^\s]+)", entry.get(field, ""))
        if m:
            return m.group(1)
    return ""


def _is_oa(entry, publisher: str) -> bool:
    for field in ["rights", "dc_rights", "prism_copyright", "license"]:
        val = str(entry.get(field, "")).lower()
        if any(k in val for k in ["creative commons", "cc-by", "cc by", "open access"]):
            return True
    for tag in entry.get("tags", []):
        if "open access" in str(tag.get("term", "")).lower():
            return True
    if entry.get("openaccess") == "1":
        return True
    return False
