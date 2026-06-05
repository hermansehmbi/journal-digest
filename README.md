# Journal Digest — Multi-Specialty Engine

A config-driven engine that emails a weekly medical-journal digest, generates
weekly CME self-assessment questions, and logs everything to a Google Sheet for
RCPSC MOC Section 2 self-reporting. One codebase serves **any number of medical
specialties** — each defined entirely by a JSON file under `specialties/`.

Launches with two specialties:

| Specialty | `--specialty` | Journals | Audio podcast | CME |
|-----------|---------------|----------|---------------|-----|
| **Anesthesia** | `anesthesia` (default) | 10 anesthesia journals | ✅ two-host TTS + GitHub Pages | ✅ (published quiz page) |
| **Electrophysiology** | `ep` | 10 EP / cardiology journals | ❌ (embedded podcast links instead) | ✅ (5 questions, inline in email) |

> The anesthesia behaviour is a faithful port of the original `anesthesia-digest`
> repo — same journals, same selection logic, same email layout. The engine was
> generalized *around* it, not on top of it.

---

## How it works

```
specialties/<name>.json   ← all specialty-specific behaviour lives here
        │
        ▼
config.py                 ← reads SPECIALTY env, loads the JSON, exposes the
                            same module-level vars the engine already used
        │
        ▼
main.py <command> --specialty=<name>
        │
   ┌────┴───────────────────────────────────────────────────────┐
   fetcher → relevance_filter (Tier-2 only) → article_selector →
   email_builder → email_sender         (+ cme_generator on Saturday)
                                         (+ gsheets / moc_tracker logging)
```

### Commands

```bash
python main.py digest        --specialty=ep   # Monday weekly article digest
python main.py saturday      --specialty=ep   # Saturday CME + week review
python main.py monthly       --specialty=anesthesia
python main.py preview       --specialty=ep   # build HTML locally, no email
python main.py preview-sat   --specialty=ep   # preview Saturday CME locally
python main.py preview-month --specialty=anesthesia
```

`--specialty=<x>` is optional; the `SPECIALTY` env var works too, and the
default is `anesthesia`.

```bash
SPECIALTY=ep python main.py preview
```

---

## The specialty JSON schema

Each `specialties/<short_name>.json` contains:

| Field | Meaning |
|-------|---------|
| `name` | Full specialty name (e.g. `"Electrophysiology"`) |
| `short_name` | Slug / `--specialty` value (e.g. `"ep"`) |
| `description` | One-line description |
| `brand_short` | Leads the email subject + audio label (`"EP"` → "EP Digest — …") |
| `brand_full` | Email header / footer title (`"EP Journal Digest"`) |
| `email_subject_prefix` | Documented alias of `brand_short` |
| `mode` | `"api"` (AI features on) or `"free"` (links + MOC only) |
| `audio_enabled` / `deepdive_enabled` / `publish_enabled` | Optional AI/GitHub-Pages feature flags |
| `cme_num_questions` | Integer (e.g. `5`) or `"per_article"` |
| `initial_lookback_days` | Backfill window for the first weeks |
| `journals[]` | Journal registry (see below) |
| `bonus_podcasts[]` | Extra publisher podcasts |
| `podcast_rotation[]` | Embedded podcast links rotated by ISO-week |
| `relevance_filter_prompt` | Tier-2 relevance classifier prompt (`""` → engine default) |
| `cme_prompt` | CME generation template with `{articles_text}` / `{num_questions}` fields (`""` → built-in anesthesia default) |
| `article_selector_prompt` | Editor guidance for the per-journal pick (`""` → default) |
| `podcast_script_prompt` | Two-host audio script guidance (only used when `audio_enabled`) |
| `google_sheet_tab_name` | This specialty's MOC log tab |
| `google_sheet_report_tab` | This specialty's print-ready report tab |
| `google_sheet_journals_tab` | This specialty's Journal Reference tab |

> **Empty prompt = engine default.** An empty/missing prompt string means "use
> the module's built-in default" (the original anesthesia prompt). That is why
> `anesthesia.json` ships with empty prompts — it guarantees the anesthesia
> output is unchanged. EP supplies its own prompts.

### Journal entry

```json
{
  "name": "EP Europace",
  "abbreviation": "Europace",
  "issn": "1099-5129",
  "rss_url": null,                 // null → fetched via Crossref by ISSN
  "website": "https://academic.oup.com/europace",
  "impact_factor": 7.5,
  "publisher": "oup",
  "fully_open_access": true,       // every article counts as OA
  "filter_required": false,        // Tier 2 (general) journals set this true
  "podcast": null
}
```

- **`rss_url: null`** — the fetcher falls back to the Crossref REST API by ISSN.
- **`fully_open_access: true`** — marks every article OA (the digest only
  features open-access articles, so this guarantees fully-OA journals appear).
- **`filter_required: true`** — Tier-2 general journals (e.g. EHJ, JACC). Their
  articles pass through `relevance_filter.py` (a fast Claude Haiku classifier)
  before selection; off-topic articles are dropped.

### Adding a new specialty

1. Copy `specialties/ep.json` to `specialties/<short_name>.json`.
2. Edit the journals, prompts, branding, and Google Sheet tab names.
3. `python verify_feeds.py --specialty=<short_name>` to check every feed.
4. `python main.py preview --specialty=<short_name>`.
5. Add a `.github/workflows/<short_name>.yml` (copy `ep.yml`).

No engine code changes are needed.

---

## EP feed notes (verified)

All EP feeds were live-verified. ScienceDirect (Elsevier) journals use
`https://rss.sciencedirect.com/publication/science/{ISSN}` and return ~80–100
recent items. Where no working RSS exists (Europace, AER, EHJ), the engine uses
the **Crossref** fallback by ISSN automatically.

| Journal | Source | Tier |
|---------|--------|------|
| Circulation: Arrhythmia & EP | AHA RSS | 1 |
| EP Europace | Crossref (fully OA) | 1 |
| Heart Rhythm | ScienceDirect RSS | 1 |
| JACC: Clinical EP | ScienceDirect RSS | 1 |
| Arrhythmia & EP Review | Crossref (fully OA) | 1 |
| Heart Rhythm O2 | ScienceDirect RSS (fully OA) | 1 |
| J Cardiovascular EP | Wiley RSS | 1 |
| J Interventional Cardiac EP | Springer RSS | 1 |
| European Heart Journal | Crossref → **relevance-filtered** | 2 |
| JACC (main) | ScienceDirect RSS → **relevance-filtered** | 2 |

**Podcasts (verified RSS feeds):** The Lead (HRS), EHRA Cardio Talk, The EP Edit,
HRX NeXt, plus the European Heart Journal Podcast as a bonus.

> The spec's "Keep the Rhythm (EHRA)" podcast does not exist as a public feed;
> the real EHRA podcast is **EHRA Cardio Talk**, which is used in its place.

> Because the digest features **open-access articles only**, weeks where the
> subscription EP journals publish no OA articles will lean on the three
> fully-OA journals (Europace, AER, Heart Rhythm O2). This is the same OA-only
> policy the anesthesia digest uses.

---

## Google Sheets MOC tracker

One spreadsheet holds **one tab per specialty** plus shared tabs:

- `Activity Log` (anesthesia) / `EP - MOC Log` (ep) — appended each Monday (articles)
  and Saturday (CME), de-duplicated by date. Columns: Date · Hours · Credits
  (self-reported = Hours × 2) · Title · Journal · APA Reference.
- `Summary & Report` / `EP - Summary & Report` — print-ready per-specialty totals.
- `<Specialty> - Journal Reference` — the journal registry metadata.
- `Summary` — **cross-specialty** aggregate of hours/credits across every
  specialty's MOC log tab (only tabs that exist are summed).
- `About` — shared explanation of the self-learning tracker.

If Google is not configured, the engine falls back to a local
`self_assessment_tracker_<specialty>.xlsx`.

### One-time setup

1. Create a **Google Cloud project**.
2. Enable the **Google Sheets API** (and Drive API).
3. Create a **service account** and download its JSON key.
4. Create a **Google Sheet** and **share it (Editor)** with the service
   account's `client_email`.
5. Add the JSON key as a GitHub Secret named **`GOOGLE_SHEETS_CREDENTIALS`**
   (the code also accepts the original name `GOOGLE_SERVICE_ACCOUNT_JSON`).
6. Set **`GOOGLE_SHEET_ID`** (the sheet's ID from its URL) — or
   `GOOGLE_DRIVE_FOLDER_ID` of a shared folder containing a sheet named
   "MOC Tracker".

> Anesthesia's tabs (`Activity Log`, `Summary & Report`, `About`) keep their
> original names, so pointing the new repo at your existing MOC sheet preserves
> the hours you've already logged. To avoid any disruption during testing, you
> can point it at a **fresh** sheet first and switch later.

---

## GitHub Secrets

| Secret | Used for |
|--------|----------|
| `GMAIL_ADDRESS` | sender (Gmail SMTP) |
| `GMAIL_APP_PASSWORD` | Gmail app password (not your login password) |
| `ANTHROPIC_API_KEY` | article selection, CME, relevance filter, audio script |
| `RECIPIENT_EMAIL` | who receives the digest |
| `GOOGLE_SHEETS_CREDENTIALS` | service-account JSON key (Google Sheets MOC) |
| `GOOGLE_SHEET_ID` *(or `GOOGLE_DRIVE_FOLDER_ID`)* | which sheet to write |

> Secret **values cannot be copied** from the old repo (GitHub stores them
> write-only). Re-add them in this repo's *Settings → Secrets and variables →
> Actions*.

---

## Testing

```bash
# Verify every RSS / Crossref / podcast feed for a specialty (+ relevance sample)
python verify_feeds.py --specialty=ep

# Smoke-test both specialties end-to-end (preview only, no email sent)
python test_all.py
```

`test_all.py` confirms anesthesia and EP each produce valid digest HTML, prints
per-journal article counts, and (with `ANTHROPIC_API_KEY` set) confirms EP CME
questions generate. The AI paths (Claude article pick, relevance filter, CME)
require `ANTHROPIC_API_KEY`; without it the engine falls back to heuristics and
`test_all.py` reports the CME step as SKIPPED.

> Local dev note: the engine targets Python 3.12 (as CI does). On macOS with an
> older default `python3`, create a venv: `python3.12 -m venv .venv &&
> .venv/bin/pip install -r requirements.txt`.

---

## Cutover plan

The original `anesthesia-digest` repo is the safety net — **leave it running**
until this repo is proven.

1. **Preview both specialties** here:
   `python test_all.py` (and `verify_feeds.py` for each).
2. **One live test per specialty** via *Actions → run workflow* (`workflow_dispatch`):
   - Anesthesia workflow → `digest`, then `saturday`.
   - EP workflow → `digest`, then `saturday`.
3. **Compare the anesthesia email** from this repo against one from the old repo
   — they should look identical (same subject, header, journals, layout).
4. Once confirmed, **disable the cron in the old `anesthesia-digest` repo**
   (comment out its schedule) — do **not** delete it.
5. **Enable the cron here**: un-comment the `schedule:` block in
   `.github/workflows/anesthesia.yml` and `ep.yml`.
6. Keep `anesthesia-digest` around for 2–3 weeks as rollback, then archive it.

> The cron triggers in both workflows ship **commented out**, so nothing runs
> automatically until you complete this plan.

---

## Project layout

```
journal-digest/
├── specialties/
│   ├── anesthesia.json        # faithful port of the original config
│   └── ep.json                # new — interventional EP
├── config.py                  # reads SPECIALTY, loads the JSON
├── fetcher.py                 # RSS + Crossref fallback, OA detection
├── relevance_filter.py        # NEW — Tier-2 relevance classifier (Haiku)
├── article_selector.py        # 1 best OA article per journal (config prompt)
├── email_builder.py           # all email HTML (config branding; inline CME)
├── email_sender.py            # Gmail SMTP
├── cme_generator.py           # CME questions (config prompt)
├── podcast_generator.py       # two-host TTS audio (anesthesia)
├── summary_generator.py, deepdive_builder.py, cme_quiz.py, publisher.py,
│   fulltext_fetcher.py        # AI/publish helpers (gated by feature flags)
├── gsheets.py                 # Google Sheets MOC — per-specialty tabs + Summary
├── moc_tracker.py             # local .xlsx fallback (per specialty)
├── main.py                    # CLI; --specialty; relevance wiring; feature gates
├── verify_feeds.py            # NEW — feed verifier
├── test_all.py                # NEW — pre-cutover smoke test
└── .github/workflows/
    ├── anesthesia.yml         # cron DISABLED until cutover
    └── ep.yml                 # cron DISABLED until cutover
```
