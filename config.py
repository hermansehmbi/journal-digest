"""
Journal Digest — Configuration (multi-specialty)
================================================
The engine is specialty-agnostic. All specialty-specific behaviour (journal
list, podcasts, prompts, branding, Google Sheet tab names) lives in a JSON file
under ``specialties/``. This module:

  1. Reads the ``SPECIALTY`` environment variable (default: "anesthesia").
  2. Loads ``specialties/{SPECIALTY}.json``.
  3. Exposes the SAME module-level variables the rest of the code already used
     (JOURNALS, BONUS_PODCASTS, MODE, INITIAL_LOOKBACK_DAYS, …) so fetcher,
     email_builder, etc. work unchanged — plus a few new specialty fields
     (BRAND_*, *_PROMPT, GOOGLE_SHEET_TAB_NAME, PODCAST_ROTATION).

No personal info is hardcoded here. Email addresses / API keys are read from
environment variables / GitHub Secrets so this repo is safe to make public.

Switch specialty with:    SPECIALTY=ep python main.py preview
or via main.py:           python main.py preview --specialty=ep
"""

import os
import json
from pathlib import Path

# ── Which specialty? ─────────────────────────────────────────────────────────
SPECIALTY = os.environ.get("SPECIALTY", "anesthesia").strip().lower()

SPECIALTIES_DIR = Path(__file__).parent / "specialties"
_SPEC_PATH = SPECIALTIES_DIR / f"{SPECIALTY}.json"
if not _SPEC_PATH.exists():
    available = ", ".join(sorted(p.stem for p in SPECIALTIES_DIR.glob("*.json")))
    raise FileNotFoundError(
        f"Unknown specialty '{SPECIALTY}'. Expected {_SPEC_PATH}. "
        f"Available: {available or '(none)'}")

with _SPEC_PATH.open(encoding="utf-8") as _f:
    _SPEC = json.load(_f)


def _spec(key, default=None):
    return _SPEC.get(key, default)


# ── Your Settings (env / GitHub Secrets) ─────────────────────────────────────
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL", os.environ.get("GMAIL_ADDRESS", ""))
TIMEZONE        = os.environ.get("TIMEZONE", "America/Toronto")  # Eastern Time

# ── Specialty identity / branding ────────────────────────────────────────────
SPECIALTY_NAME        = _spec("name", "Anesthesia")            # e.g. "Electrophysiology"
SPECIALTY_SHORT       = _spec("short_name", SPECIALTY)         # e.g. "ep"
SPECIALTY_DESCRIPTION = _spec("description", "")
# BRAND_SHORT leads the email subject + audio label ("Anesthesia Digest — …").
BRAND_SHORT = _spec("brand_short", _spec("email_subject_prefix", SPECIALTY_NAME))
# BRAND_FULL is the email header / footer title.
BRAND_FULL  = _spec("brand_full", f"{SPECIALTY_NAME} Journal Digest")
EMAIL_SUBJECT_PREFIX = _spec("email_subject_prefix", BRAND_SHORT)

# ── Mode ─────────────────────────────────────────────────────────────────────
# "free"  → article links + podcast links + MOC tracker only
# "api"   → adds AI audio summary + AI-generated CME questions
MODE = os.environ.get("MODE", _spec("mode", "api")).strip().lower()

# ── Optional AI feature flags (per specialty) ────────────────────────────────
# Anesthesia ships with everything on. Electrophysiology launches without TTS
# audio / Deep Dive / GitHub Pages publishing — the Monday email works with just
# article links + embedded podcast links, and Saturday CME still runs.
AUDIO_ENABLED    = bool(_spec("audio_enabled", True))
DEEPDIVE_ENABLED = bool(_spec("deepdive_enabled", True))
PUBLISH_ENABLED  = bool(_spec("publish_enabled", True))

# ── Claude API (only needed if MODE = "api") ─────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Strong model — used where reasoning quality matters most.
CLAUDE_MODEL = "claude-sonnet-4-6"
# Cheaper/faster model — article selection, CME, podcast script, relevance filter.
CLAUDE_MODEL_FAST = "claude-haiku-4-5"
# Dedicated model for the relevance filter (fast/cheap classification).
RELEVANCE_MODEL = "claude-haiku-4-5-20251001"

# Cap how much article source text is sent to the API per CME article (chars).
CME_SOURCE_CHARS = 1600

# ── Specialty prompts (empty string → engine's built-in default) ─────────────
# An empty/missing prompt means "use the module's built-in default" (which is the
# original anesthesia prompt), so the anesthesia output is byte-for-byte
# unchanged. EP and future specialties supply their own prompts in JSON.
RELEVANCE_FILTER_PROMPT = _spec("relevance_filter_prompt", "") or ""
CME_PROMPT              = _spec("cme_prompt", "") or ""
ARTICLE_SELECTOR_PROMPT = _spec("article_selector_prompt", "") or ""
PODCAST_SCRIPT_PROMPT   = _spec("podcast_script_prompt", "") or ""
# CME question count: an int (e.g. 5) or the string "per_article" (one per article).
CME_NUM_QUESTIONS = _spec("cme_num_questions", "per_article")

# ── Audio Settings (only if MODE = "api" and AUDIO_ENABLED) ──────────────────
HOST_A_VOICE = "en-US-AndrewMultilingualNeural"  # Host A — male, warm
HOST_B_VOICE = "en-US-AvaMultilingualNeural"     # Host B — female, bright
TTS_VOICE = HOST_A_VOICE
TTS_VOICE_ALT = HOST_B_VOICE
TTS_RATE = "+10%"
PODCAST_MINUTES_TARGET = 15
PODCAST_WORD_TARGET = 2400

# ── Music / Mixing ───────────────────────────────────────────────────────────
INTRO_MUSIC = "assets/intro_music.mp3"
OUTRO_MUSIC = "assets/outro_music.mp3"
MUSIC_DUCK_DB = -9
INTRO_SOLO_MS = 6000
OUTRO_SOLO_MS = 6000
MUSIC_CROSSFADE_MS = 1200

# ── Final loudness normalization ─────────────────────────────────────────────
PODCAST_TARGET_DBFS = -16.0
PODCAST_PEAK_CEILING_DBFS = -1.0

# ── GitHub Pages (inline podcast playback) ───────────────────────────────────
GITHUB_PAGES_URL = os.environ.get("GITHUB_PAGES_URL", "")
DOCS_DIR = "docs"
AUDIO_SUBDIR = "audio"

# ── Initial Run Settings ─────────────────────────────────────────────────────
INITIAL_LOOKBACK_DAYS = int(_spec("initial_lookback_days", 30))

# ── Journal Registry (from the specialty JSON) ───────────────────────────────
JOURNALS = _spec("journals", [])
JOURNAL_COUNT = len(JOURNALS)

# ── Bonus Podcasts (free, from publishers) ───────────────────────────────────
BONUS_PODCASTS = _spec("bonus_podcasts", [])

# ── Podcast rotation (embedded links, week-of-year rotation) ─────────────────
PODCAST_ROTATION = _spec("podcast_rotation", [])

# ── Google Sheets MOC tracker (per-specialty tabs) ───────────────────────────
# One spreadsheet, one tab per specialty. Anesthesia keeps the original tab
# names so its existing live data is preserved; new specialties get namespaced
# tabs ("EP - MOC Log", "EP - Journal Reference", "EP - Summary & Report").
GOOGLE_SHEET_TAB_NAME    = _spec("google_sheet_tab_name", "Activity Log")
GOOGLE_SHEET_REPORT_TAB  = _spec("google_sheet_report_tab", "Summary & Report")
GOOGLE_SHEET_JOURNALS_TAB = _spec("google_sheet_journals_tab",
                                  f"{BRAND_SHORT} - Journal Reference")


# ── Cross-specialty roster (for the aggregate "Summary" tab) ─────────────────
def all_specialties() -> list[dict]:
    """Every specialty's identity + Google Sheet tab names, by scanning
    specialties/*.json. Used to build the cross-specialty Summary tab."""
    out = []
    for p in sorted(SPECIALTIES_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        brand = d.get("brand_short") or d.get("email_subject_prefix") or d.get("name", p.stem)
        out.append({
            "key": p.stem,
            "name": d.get("name", p.stem),
            "brand_short": brand,
            "log_tab": d.get("google_sheet_tab_name", "Activity Log"),
            "report_tab": d.get("google_sheet_report_tab", "Summary & Report"),
        })
    return out


ALL_SPECIALTIES = all_specialties()

# ── Schedule Reference ───────────────────────────────────────────────────────
# Monday        → weekly digest (articles + podcasts + audio if API/enabled)
# Saturday      → weekly CME questions (API) + week's highlights
# 1st of month  → monthly top-5 + MOC tracker refresh
