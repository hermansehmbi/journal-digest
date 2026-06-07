"""
Journal Digest — Full-Text Resolver
===================================
Find the best available full text for an article, in priority order:

  1. Unpaywall (free; an email is required as a polite identifier, NOT a login —
     we pass GMAIL_ADDRESS). Uses is_oa + best_oa_location.
  2. Europe PMC full text (PMCID -> JATS XML).
  3. Publisher-page scrape (works for open publishers; OUP/AHA/Elsevier block).
  4. Abstract fallback — flagged abstract_only=True so the summary/email can say
     "based on abstract only".

Two entry points:
  - availability(article)      cheap check (Unpaywall, then PMC) -> where/whether
                               full text exists. Used by `scan` and scoring.
  - get_fulltext(article)      actually retrieves the body text for summaries.

Reuses the Europe-PMC / JATS / scrape helpers in fulltext_fetcher.
"""

from __future__ import annotations

import os
import re
import json
import logging
import unicodedata
import concurrent.futures
from pathlib import Path

import httpx

import fulltext_fetcher as ff

logger = logging.getLogger(__name__)
_TIMEOUT = 25
UNPAYWALL = "https://api.unpaywall.org/v2"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CROSSREF_WORKS = "https://api.crossref.org/works"
DOI_CACHE_FILE = "doi_cache.json"


def key(article: dict) -> str:
    """Stable per-article key (doi:… or url:…) used for maps + dedup."""
    return ff._key(article)


def _email() -> str:
    """Polite identifier for Unpaywall (Task 5: reuse GMAIL_ADDRESS)."""
    try:
        from config import SENDER_EMAIL, RECIPIENT_EMAIL
    except Exception:
        SENDER_EMAIL = RECIPIENT_EMAIL = ""
    return (os.environ.get("GMAIL_ADDRESS") or SENDER_EMAIL or RECIPIENT_EMAIL
            or "journal-digest@users.noreply.github.com")


# ── DOI resolution (Crossref title-lookup, cached) ───────────────────────────
# Publisher feeds (esp. the ScienceDirect ISSN feeds) often omit DOIs, which
# makes articles invisible to Unpaywall/PMC. Resolve a DOI by matching the title
# within the journal's ISSN on Crossref, so full-text lookup can proceed.

def _norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _title_overlap(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def _issn_for(article: dict) -> str:
    try:
        from config import JOURNALS
    except Exception:
        return ""
    for j in JOURNALS:
        if j.get("abbreviation") == article.get("journal_abbr"):
            return j.get("issn", "") or ""
    return ""


def _crossref_doi_by_title(title: str, issn: str) -> str:
    if not title:
        return ""
    params = {"query.bibliographic": title, "rows": 3, "select": "DOI,title"}
    if issn:
        params["filter"] = f"issn:{issn}"
    try:
        r = httpx.get(CROSSREF_WORKS, params=params,
                      headers={"User-Agent": f"JournalDigest/1.0 (mailto:{_email()})"},
                      timeout=_TIMEOUT)
        if r.status_code != 200:
            return ""
        items = r.json().get("message", {}).get("items", [])
    except Exception:
        return ""
    nt = _norm_title(title)
    for it in items:
        cand = (it.get("title") or [""])[0]
        nc = _norm_title(cand)
        if not nc:
            continue
        if (nc == nt or (len(nt) > 40 and nc.startswith(nt[:40]))
                or (len(nc) > 40 and nt.startswith(nc[:40]))
                or _title_overlap(nt, nc) >= 0.8):
            return it.get("DOI", "") or ""
    return ""


def resolve_dois(articles: list, workers: int = 8) -> int:
    """For articles lacking a DOI, resolve one via Crossref (title + ISSN) and
    set article['doi'] in place. Cached on disk. Returns how many were resolved."""
    cache = _load_doi_cache()
    todo = [a for a in articles if not ff._doi(a)]
    if not todo:
        return 0

    def work(a):
        title = a.get("title", "")
        issn = _issn_for(a)
        ck = f"{issn}|{_norm_title(title)}"
        if ck in cache:
            return a, cache[ck]
        doi = _crossref_doi_by_title(title, issn)
        cache[ck] = doi
        return a, doi

    resolved = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for a, doi in ex.map(work, todo):
            if doi:
                a["doi"] = doi
                resolved += 1
    _save_doi_cache(cache)
    logger.info(f"DOI resolver: filled {resolved}/{len(todo)} missing DOIs via Crossref")
    return resolved


def _load_doi_cache() -> dict:
    p = Path(DOI_CACHE_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_doi_cache(cache: dict):
    try:
        Path(DOI_CACHE_FILE).write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


# ── Source lookups ───────────────────────────────────────────────────────────

def _unpaywall(doi: str) -> dict | None:
    """Return {is_oa, oa_url, pdf_url, host_type} or None."""
    if not doi:
        return None
    try:
        r = httpx.get(f"{UNPAYWALL}/{doi}", params={"email": _email()},
                      timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
    except Exception as e:
        logger.warning(f"  Unpaywall lookup failed for {doi}: {e}")
        return None
    loc = d.get("best_oa_location") or {}
    return {
        "is_oa": bool(d.get("is_oa")),
        "oa_url": loc.get("url_for_landing_page") or loc.get("url"),
        "pdf_url": loc.get("url_for_pdf"),
        "host_type": loc.get("host_type"),
    }


def _pmcid_via_ncbi(doi: str) -> str | None:
    """Return a PMCID if the DOI is in the PMC open-access corpus, else None."""
    if not doi:
        return None
    try:
        r = httpx.get(NCBI_ESEARCH, params={"db": "pmc", "term": f"{doi}[DOI]",
                                            "retmode": "json"}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        return ("PMC" + ids[0]) if ids else None
    except Exception:
        return None


# ── Availability (cheap; for scan + scoring) ─────────────────────────────────

# Publishers that serve a 403/anti-bot page to automated requests — an OA copy
# hosted ONLY here ("publisher" host_type) is open access but NOT fetchable by
# us. The OA url is often a doi.org redirect, so we key off the DOI prefix.
_BLOCKED_DOMAINS = ("academic.oup.com", "ahajournals.org", "sciencedirect.com",
                    "onlinelibrary.wiley.com", "journals.lww.com", "rapm.bmj.com")
_BLOCKED_DOI_PREFIXES = {
    "10.1093": "Oxford UP", "10.1161": "AHA", "10.1016": "Elsevier",
    "10.1002": "Wiley", "10.1111": "Wiley", "10.1097": "Wolters Kluwer/LWW",
}


def _fetchable(doi: str, url: str, host_type: str) -> bool:
    """Can we actually retrieve the body at this OA location? Repositories (PMC,
    institutional) yes; open publishers yes; the known anti-bot publishers no
    (identified by DOI prefix, since the OA url is usually a doi.org redirect)."""
    if host_type == "repository":
        return True
    prefix = (doi or "").split("/", 1)[0]
    if prefix in _BLOCKED_DOI_PREFIXES:
        return False
    if url and any(b in url.lower() for b in _BLOCKED_DOMAINS):
        return False
    return bool(url)


def availability(article: dict) -> dict:
    """Where is *fetchable* full text available? {has_doi, is_oa, has_fulltext,
    oa_blocked, via}. has_fulltext means we can actually retrieve the body — OA
    that lives only behind a blocking publisher counts as is_oa but NOT
    has_fulltext. Unpaywall first, then a direct PMC check. No body download.
    """
    doi = ff._doi(article)
    res = {"has_doi": bool(doi), "is_oa": bool(article.get("is_open_access")),
           "has_fulltext": False, "oa_blocked": False, "via": None}
    if not doi:
        return res

    up = _unpaywall(doi)
    if up and up["is_oa"]:
        res["is_oa"] = True
        url = up["oa_url"] or up["pdf_url"] or ""
        if _fetchable(doi, url, up["host_type"]):
            res["has_fulltext"] = True
            res["via"] = f"unpaywall/{up['host_type'] or 'oa'}"
            return res
        res["oa_blocked"] = True  # OA, but only at a publisher we can't scrape

    # Backstop: a PMC copy may exist even when Unpaywall's "best" was a blocked
    # publisher page.
    pmcid = _pmcid_via_ncbi(doi)
    if pmcid:
        res["has_fulltext"] = True
        res["via"] = "pmc"
        res["is_oa"] = True
        res["oa_blocked"] = False
    return res


def availability_map(articles: list, workers: int = 8) -> dict:
    """Concurrent availability() for many articles, keyed by key(article)."""
    out: dict = {}

    def work(a):
        return key(a), availability(a)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for k, v in ex.map(work, articles):
            out[k] = v
    return out


# ── Full retrieval (for summaries) ───────────────────────────────────────────

def get_fulltext(article: dict) -> dict:
    """Retrieve body text. Returns
    {source, is_open_access, abstract_only, text, via}.
    source is "fulltext" / "abstract" / "none"."""
    doi = ff._doi(article)

    # 1. Unpaywall OA location.
    up = _unpaywall(doi) if doi else None
    if up and up["is_oa"]:
        url = up["oa_url"] or ""
        m = re.search(r"PMC(\d+)", url or "")
        if m:  # OA copy lives in PMC — grab clean JATS.
            t = _pmc_jats("PMC" + m.group(1))
            if t:
                return _ft(t, True, "unpaywall/pmc")
        if url:
            t = _fetch_html(url)
            if t:
                return _ft(t, True, f"unpaywall/{up['host_type'] or 'oa'}")

    # 2. Europe PMC (PMCID -> JATS).
    rec = ff._epmc_lookup(doi) if doi else None
    if rec and rec.get("pmcid"):
        t = _pmc_jats(rec["pmcid"])
        if t:
            return _ft(t, True, "europepmc")

    # 3. Publisher page scrape (open publishers only).
    scraped = ff._scrape_publisher(article)
    if scraped:
        return _ft(scraped, bool(article.get("is_open_access")), "scrape")

    # 4. Abstract fallback.
    abstract = ff._clean((rec or {}).get("abstractText", "")) or \
        ff._clean(article.get("abstract", ""))
    return {"source": "abstract" if abstract else "none",
            "is_open_access": bool(article.get("is_open_access")),
            "abstract_only": True, "text": abstract[:ff.MAX_TEXT], "via": "abstract"}


def _pmc_jats(pmcid: str) -> str | None:
    xml = ff._epmc_fulltext(pmcid)
    secs = ff._parse_jats(xml) if xml else {}
    if secs:
        t = ff._format_sections(secs)
        if t.strip():
            logger.info(f"  Full text via PMC {pmcid} ({len(t)} chars)")
            return t
    return None


def _fetch_html(url: str) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": ff._BROWSER_UA,
                                    "Accept": "text/html,application/xhtml+xml"},
                      timeout=35, follow_redirects=True)
    except Exception as e:
        logger.warning(f"  OA HTML fetch failed ({url[:55]}…): {e}")
        return None
    if r.status_code != 200:
        return None
    low = r.text.lower()
    if len(r.text) < 30000 and any(m in low for m in ff._BLOCK_MARKERS):
        return None
    t = ff._extract_article_text(r.text)
    if t and len(t) >= ff._SCRAPE_MIN_CHARS:
        logger.info(f"  Full text via OA page ({len(t)} chars): {url[:55]}…")
        return t[:ff.MAX_TEXT]
    return None


def _ft(text: str, oa: bool, via: str) -> dict:
    return {"source": "fulltext", "is_open_access": oa, "abstract_only": False,
            "text": text[:ff.MAX_TEXT], "via": via}
