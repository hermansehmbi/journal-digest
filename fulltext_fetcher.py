"""
Anesthesia Journal Digest — Full-Text Fetcher
==============================================
For the ~10 articles used in the weekly CME quiz, attempt to retrieve the
article's full text so explanations can quote real effect sizes and statistics.

Strategy (respectful, structured, cached):
  1. Look the article up in Europe PMC by DOI (a clean public REST API).
  2. If it is OPEN ACCESS and a PMCID exists, fetch the JATS full-text XML and
     extract the Results / Findings / Discussion / Conclusions / Recommendations
     sections.
  3. Otherwise (paywalled, or no full text available) fall back to the abstract.

Results are cached to fulltext_cache.json keyed by DOI/URL so Monday, Thursday,
and Saturday runs never re-fetch the same article. We NEVER fabricate text — if
nothing can be retrieved, the caller is told the source is "abstract"/"none" and
must avoid inventing numbers.
"""

import os
import re
import json
import logging
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

CACHE_FILE = "fulltext_cache.json"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_TEXT = 18000  # cap stored/returned text (raised so Methods + full Results,
                  # with their sample sizes and statistics, reach the summarizer)

try:
    from config import RECIPIENT_EMAIL as _MAILTO
except Exception:
    _MAILTO = ""
_MAILTO = _MAILTO or "anesthesia-digest@example.com"
USER_AGENT = f"AnesthesiaDigest/1.0 (mailto:{_MAILTO}; educational CME summaries)"

# Section titles we care about, in the order we prefer to present them. Methods
# (incl. the statistical-analysis subsection) comes first so sample sizes, study
# design, and the statistical tests used reach the summarizer.
_SECTION_ORDER = [
    ("methods", ("method", "material", "patients and", "statistical",
                 "study design", "design and")),
    ("results", ("result", "finding", "outcome")),
    ("discussion", ("discussion", "interpretation")),
    ("conclusions", ("conclusion",)),
    ("recommendations", ("recommendation",)),
]


def get_article_text(article: dict) -> dict:
    """Return {"source", "is_open_access", "text"} for one article (cached).

    source is "fulltext" (OA full text parsed), "abstract" (abstract only), or
    "none" (nothing available).
    """
    key = _key(article)
    cache = _load_cache()
    if key and key in cache:
        return cache[key]

    result = _fetch(article)

    if key:
        cache[key] = result
        _save_cache(cache)
    return result


def get_many(articles: list) -> list:
    """Fetch (cached) full text for several articles; pairs with the inputs."""
    return [get_article_text(a) for a in articles]


# ── Fetch pipeline ───────────────────────────────────────────────────────────

def _fetch(article: dict) -> dict:
    doi = _doi(article)
    rec = _epmc_lookup(doi) if doi else None

    if rec:
        is_oa = rec.get("isOpenAccess") == "Y"
        pmcid = rec.get("pmcid")
        if is_oa and pmcid:
            xml = _epmc_fulltext(pmcid)
            sections = _parse_jats(xml) if xml else {}
            if sections:
                text = _format_sections(sections)
                if text.strip():
                    logger.info(f"  Full text via Europe PMC: {pmcid} "
                                f"({len(text)} chars)")
                    return {"source": "fulltext", "is_open_access": True,
                            "text": text[:MAX_TEXT]}
        # PMC full text unavailable — try the publisher page directly (works for
        # open publishers like Radcliffe/AER; OUP/AHA/Elsevier usually block bots).
        scraped = _scrape_publisher(article)
        if scraped:
            return {"source": "fulltext", "is_open_access": is_oa, "text": scraped}
        abstract = _clean(rec.get("abstractText", "")) or _clean(article.get("abstract", ""))
        logger.info(f"  Abstract only for DOI {doi} (open_access={is_oa})")
        return {"source": "abstract" if abstract else "none",
                "is_open_access": is_oa, "text": abstract[:MAX_TEXT]}

    # No Europe PMC record — try the publisher page, then fall back to abstract.
    scraped = _scrape_publisher(article)
    if scraped:
        return {"source": "fulltext",
                "is_open_access": bool(article.get("is_open_access")), "text": scraped}
    abstract = _clean(article.get("abstract", ""))
    return {"source": "abstract" if abstract else "none",
            "is_open_access": bool(article.get("is_open_access")),
            "text": abstract[:MAX_TEXT]}


# ── Publisher-page fallback (best-effort HTML scrape) ─────────────────────────

# Set FULLTEXT_SCRAPE=0 to disable. Only the more open publishers (e.g.
# Radcliffe/AER) return article HTML; OUP/AHA/Elsevier serve a 403/anti-bot page,
# which we detect and skip — falling back to the abstract.
_SCRAPE_ENABLED = os.environ.get("FULLTEXT_SCRAPE", "1") != "0"
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_BLOCK_MARKERS = ("just a moment", "cf-browser-verification", "/cdn-cgi/",
                  "captcha", "access denied", "enable javascript and cookies",
                  "are you a human", "are you a robot")
# A full article yields far more text than an abstract-only landing page (~2 KB),
# so require a generous minimum before trusting a scrape as "full text".
_SCRAPE_MIN_CHARS = 4000


def _scrape_publisher(article: dict) -> str | None:
    """Best-effort: fetch the article's publisher page and extract its body text.
    Returns the text on success, or None if blocked / too thin / unavailable."""
    if not _SCRAPE_ENABLED:
        return None
    url = (article.get("url") or "").strip()
    if not url:
        return None
    try:
        resp = httpx.get(url, headers={"User-Agent": _BROWSER_UA,
                                       "Accept": "text/html,application/xhtml+xml"},
                         timeout=35, follow_redirects=True)
    except Exception as e:
        logger.warning(f"  Publisher fetch failed ({url[:55]}…): {e}")
        return None
    if resp.status_code != 200:
        logger.info(f"  Publisher page HTTP {resp.status_code} (blocked?): {url[:55]}…")
        return None
    page = resp.text
    low = page.lower()
    if len(page) < 30000 and any(m in low for m in _BLOCK_MARKERS):
        logger.info(f"  Publisher page anti-bot block: {url[:55]}…")
        return None
    text = _extract_article_text(page)
    if text and len(text) >= _SCRAPE_MIN_CHARS:
        logger.info(f"  Full text via publisher page ({len(text)} chars): {url[:55]}…")
        return text[:MAX_TEXT]
    return None


def _extract_article_text(page: str) -> str:
    """Pull readable body text from a publisher HTML page: drop scripts/nav/etc.,
    then collect heading + paragraph text. Crude but adequate for summarization."""
    h = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|figure)\b.*?</\1>",
               " ", page)
    parts = []
    for tag, inner in re.findall(r"(?is)<(p|h2|h3|h4|li)\b[^>]*>(.*?)</\1>", h):
        t = _clean(inner)
        if tag.lower().startswith("h"):
            if t:
                parts.append(t)
        elif len(t) >= 40:
            parts.append(t)
    return "\n".join(parts).strip()


def _epmc_lookup(doi: str) -> dict | None:
    """Find an article's Europe PMC core record by DOI."""
    try:
        resp = httpx.get(
            f"{EPMC}/search",
            params={"query": f'DOI:"{doi}"', "format": "json",
                    "resultType": "core", "pageSize": 1},
            headers={"User-Agent": USER_AGENT}, timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("resultList", {}).get("result", [])
        return results[0] if results else None
    except Exception as e:
        logger.warning(f"  Europe PMC lookup failed for {doi}: {e}")
        return None


def _epmc_fulltext(pmcid: str) -> str | None:
    """Fetch JATS full-text XML for an open-access PMCID.

    Primary: Europe PMC ``/{PMCID}/fullTextXML``. Fallback: NCBI E-utilities
    efetch for the PMC OA subset (keyed by the numeric id).
    """
    pmcid = pmcid.strip()
    num = pmcid[3:] if pmcid.upper().startswith("PMC") else pmcid
    urls = [
        f"{EPMC}/{pmcid}/fullTextXML",
        f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=pmc&id={num}&retmode=xml",
    ]
    for url in urls:
        try:
            resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                             timeout=25, follow_redirects=True)
            if resp.status_code == 200 and ("<sec" in resp.text or "<body" in resp.text):
                return resp.text
        except Exception as e:
            logger.warning(f"  Full-text XML fetch failed ({url[:60]}…): {e}")
    return None


# ── JATS parsing ─────────────────────────────────────────────────────────────

def _parse_jats(xml: str) -> dict:
    """Extract the meaningful sections from JATS full-text XML.

    Returns an ordered dict-like mapping {canonical_section: text}.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        logger.warning(f"  JATS parse error: {e}")
        return {}

    body = _localfind(root, "body")
    if body is None:
        return {}

    found = {}
    for sec in _local_iter(body, "sec"):
        title_el = _localfind(sec, "title")
        title = _text_of(title_el).lower() if title_el is not None else ""
        if not title:
            continue
        for canon, keys in _SECTION_ORDER:
            if canon in found:
                continue
            if any(k in title for k in keys):
                text = _clean(_text_of(sec))
                if len(text) > 60:
                    found[canon] = text
                break
    return found


def _format_sections(sections: dict) -> str:
    parts = []
    for canon, _ in _SECTION_ORDER:
        if canon in sections:
            parts.append(f"{canon.upper()}:\n{sections[canon]}")
    return "\n\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _local(tag: str) -> str:
    """Strip any XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1]


def _localfind(el, name: str):
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _local_iter(el, name: str):
    for child in el.iter():
        if _local(child.tag) == name:
            yield child


def _text_of(el) -> str:
    if el is None:
        return ""
    return " ".join(t for t in el.itertext())


def _doi(article: dict) -> str:
    doi = (article.get("doi") or "").strip()
    if doi:
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
        return doi
    for field in ("url", "id"):
        m = re.search(r"(10\.\d{4,}/[^\s?#]+)", article.get(field, "") or "")
        if m:
            return m.group(1)
    return ""


def _key(article: dict) -> str:
    doi = _doi(article)
    if doi:
        return "doi:" + doi.lower()
    url = article.get("url") or ""
    return "url:" + url if url else ""


def _clean(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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
