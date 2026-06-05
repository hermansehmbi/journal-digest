"""
Anesthesia Journal Digest — Live Self-Assessment Tracker in Google Sheets
=========================================================================
Rolling, never-resetting self-assessment tracker in a Google Sheet, plus a Drive
folder structure for future certificate storage.

Auth: a Google Cloud SERVICE ACCOUNT. Two environment variables / GitHub Secrets:
  - GOOGLE_SERVICE_ACCOUNT_JSON : the full service-account key JSON (as a string)
  - GOOGLE_DRIVE_FOLDER_ID      : ID of a Drive folder shared (Editor) with the
                                  service account's client_email.
  - GOOGLE_SHEET_ID (optional)  : open this sheet directly by key.

Three tabs (all text Arial, 12pt base; titles/headers larger):
  1. "About"            — what it is + how to use it (RCPSC MOC Section 2
                          self-reporting; not accredited).
  2. "Activity Log"     — Date | Hours | Credits (self-reported) | Title |
                          Journal | APA Reference. Append-only; user-fillable.
  3. "Summary & Report" — print-ready: a colored "print for your MOC submission"
                          banner + auto-summed totals, then an itemized credits
                          table mirroring every Activity Log entry.

Design:
  - APPEND-ONLY Activity Log (manual edits to Hours/Notes persist).
  - De-duplicated by date so re-runs never double-log a digest.
  - Totals + the itemized table are LIVE formulas over the Activity Log.
If anything Google-related fails, callers fall back to the local xlsx tracker.
"""

import os
import json
import logging

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_NAME = "MOC Tracker"   # existing live file name (kept for lookup)
CERT_FOLDER_NAME = "Certificates"

# Tab names are per-specialty (driven by the active specialty's JSON). Anesthesia
# keeps the original tab names so its existing live data is preserved; new
# specialties get namespaced tabs (e.g. "EP - MOC Log", "EP - Summary & Report",
# "EP - Journal Reference"). One spreadsheet holds every specialty's tabs plus a
# single shared "About" tab and a cross-specialty "Summary" tab.
ABOUT_SHEET = "About"
ACTIVITY_SHEET = config.GOOGLE_SHEET_TAB_NAME            # this specialty's MOC log
REPORT_SHEET = config.GOOGLE_SHEET_REPORT_TAB           # this specialty's report
JOURNALS_SHEET = config.GOOGLE_SHEET_JOURNALS_TAB       # this specialty's journals
CROSS_SUMMARY_SHEET = "Summary"                          # aggregates ALL specialties

# Activity Log column order: Date | Hours | Credits | Title | Journal | APA
ACTIVITY_HEADERS = ["Date", "Hours", "Credits (self-reported)", "Title",
                    "Journal", "APA Reference"]

# Bounded ranges for the live Summary formulas (plenty for years of logging).
_LOG_MAXROW = 2000

# Base font everywhere is Arial 12 (titles/headers kept proportionally larger).
FONT = "Arial"
BASE = 12

# Colours (Sheets API wants 0–1 floats).
NAVY = {"red": 0.102, "green": 0.322, "blue": 0.463}   # 1A5276
REDCTA = {"red": 0.753, "green": 0.224, "blue": 0.169}  # C0392B
LIGHT = {"red": 0.918, "green": 0.945, "blue": 0.973}  # EAF1F8
WHITE = {"red": 1, "green": 1, "blue": 1}
GREY = {"red": 0.53, "green": 0.53, "blue": 0.53}


# The service-account key JSON may be supplied under either env var name:
# GOOGLE_SERVICE_ACCOUNT_JSON (original) or GOOGLE_SHEETS_CREDENTIALS (alias).
_KEY_ENV_VARS = ("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEETS_CREDENTIALS")


def _key_json() -> str:
    for name in _KEY_ENV_VARS:
        val = os.environ.get(name)
        if val:
            return val
    return ""


def is_configured() -> bool:
    """True only if the key plus a way to locate the sheet are present."""
    has_key = bool(_key_json())
    has_target = bool(os.environ.get("GOOGLE_SHEET_ID")
                      or os.environ.get("GOOGLE_DRIVE_FOLDER_ID"))
    return has_key and has_target


# ── Auth / handles ───────────────────────────────────────────────────────────

def _creds():
    from google.oauth2.service_account import Credentials
    info = json.loads(_key_json())
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def _open():
    """Return (gspread_client, drive_service, folder_id)."""
    import gspread
    from googleapiclient.discovery import build
    creds = _creds()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    return gc, drive, folder_id


def _find_in_folder(drive, folder_id, name, mime):
    q = (f"name = '{name}' and '{folder_id}' in parents "
         f"and mimeType = '{mime}' and trashed = false")
    res = drive.files().list(
        q=q, fields="files(id, name)", spaces="drive",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def _ensure_cert_folder(drive, folder_id):
    if not folder_id:
        return None
    fid = _find_in_folder(drive, folder_id, CERT_FOLDER_NAME,
                          "application/vnd.google-apps.folder")
    if fid:
        return fid
    meta = {"name": CERT_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [folder_id]}
    f = drive.files().create(body=meta, fields="id",
                             supportsAllDrives=True).execute()
    logger.info(f"Created Drive subfolder '{CERT_FOLDER_NAME}'")
    return f["id"]


def _open_spreadsheet(gc, drive, folder_id):
    # Prefer an explicit GOOGLE_SHEET_ID; else find by name in the shared folder.
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    if sheet_id:
        return gc.open_by_key(sheet_id)
    if folder_id:
        sid = _find_in_folder(drive, folder_id, SPREADSHEET_NAME,
                              "application/vnd.google-apps.spreadsheet")
        if sid:
            return gc.open_by_key(sid)
    raise RuntimeError(
        "Could not locate the tracker spreadsheet. Set GOOGLE_SHEET_ID, or share "
        f"a sheet named '{SPREADSHEET_NAME}' in the GOOGLE_DRIVE_FOLDER_ID folder.")


def connect():
    """Open and return the tracker spreadsheet handle."""
    gc, drive, folder_id = _open()
    _ensure_cert_folder(drive, folder_id)
    return _open_spreadsheet(gc, drive, folder_id)


# ── APA reference ────────────────────────────────────────────────────────────

def _apa_authors(raw: str) -> str:
    names = [n.strip() for n in (raw or "").replace(" and ", ";").split(";") if n.strip()]
    out = []
    for n in names:
        parts = n.split()
        if len(parts) == 1:
            out.append(parts[0]); continue
        surname = parts[-1]
        initials = " ".join(f"{p[0].upper()}." for p in parts[:-1] if p)
        out.append(f"{surname}, {initials}".strip())
    if not out:
        return ""
    if len(out) == 1:
        return out[0]
    if len(out) <= 20:
        return ", ".join(out[:-1]) + ", & " + out[-1]
    return ", ".join(out[:19]) + ", … " + out[-1]


def apa_reference(art: dict) -> str:
    """Single-line APA-style reference from an article dict."""
    authors = _apa_authors(art.get("authors", ""))
    date_str = str(art.get("date_str") or art.get("date") or "")
    year = date_str[:4] if len(date_str) >= 4 and date_str[:4].isdigit() else "n.d."
    title = (art.get("title") or "").strip().rstrip(".")
    journal = art.get("journal") or art.get("journal_abbr") or ""
    doi = (art.get("doi") or "").strip()
    link = f"https://doi.org/{doi}" if doi else (art.get("url") or "")
    ref = (f"{authors} " if authors else "") + f"({year}). {title}. {journal}."
    if link:
        ref += f" {link}"
    return ref.strip()


def _activity_row(art: dict, date: str) -> list:
    """One Activity Log row in the new layout. Credits is a self-reported formula."""
    return [date, "", "__CREDITS__",
            (art.get("title", "") or "")[:300],
            art.get("journal_abbr", "") or art.get("journal", ""),
            apa_reference(art)]


# ── Formatting helpers ───────────────────────────────────────────────────────

def _cell(*, bold=None, italic=None, size=BASE, color=None, bg=None,
          align=None, valign=None, wrap=None):
    tf = {"fontFamily": FONT, "fontSize": size}
    if bold is not None:
        tf["bold"] = bold
    if italic is not None:
        tf["italic"] = italic
    if color is not None:
        tf["foregroundColor"] = color
    fmt = {"textFormat": tf}
    if bg is not None:
        fmt["backgroundColor"] = bg
    if align is not None:
        fmt["horizontalAlignment"] = align
    if valign is not None:
        fmt["verticalAlignment"] = valign
    if wrap is not None:
        fmt["wrapStrategy"] = wrap
    return fmt


def _set_col_widths(sh, ws, widths: dict):
    """widths: {0-based col index: pixels}. One batch request."""
    reqs = []
    for col, px in widths.items():
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                      "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    if reqs:
        sh.batch_update({"requests": reqs})


def _unmerge_all(sh, ws):
    sh.batch_update({"requests": [{"unmergeCells": {"range": {"sheetId": ws.id}}}]})


# ── Structure ────────────────────────────────────────────────────────────────

def _get_or_add_ws(sh, title, rows=200, cols=8):
    import gspread
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_about(sh):
    """Create/refresh the About tab (Arial ≥14)."""
    ws = _get_or_add_ws(sh, ABOUT_SHEET, rows=60, cols=4)
    ws.clear()
    _unmerge_all(sh, ws)
    blocks = [
        ("Self-Assessment Tracker", "TITLE"),
        ("Journal Digest — Self-Learning", "SUB"),
        ("What this is", "H"),
        ("A simple log for your own self-directed study of the medical literature. "
         "Use it to record the articles you read and reflect on each week, the time you "
         "spend, and the self-reported credits you choose to claim.", "P"),
        ("Purpose", "H"),
        ("It keeps a tidy, printable record of self-learning that may be eligible for "
         "self-reporting under RCPSC MOC Section 2 (Self-Learning). Logging an activity "
         "here is a personal record only — you are responsible for confirming your own "
         "eligibility before submitting anything to the Royal College.", "P"),
        ("How to use it", "H"),
        ("1. Open the “Activity Log” tab. Each row is one activity; new article picks "
         "are added automatically, and you can add your own rows at the bottom.", "P"),
        ("2. Enter the Hours you spent. The Credits (self-reported) column fills in "
         "automatically at the Section 2 rate of 2 credits per hour (copy the formula "
         "down for rows you add).", "P"),
        ("3. The “Summary & Report” tab auto-totals your hours, credits, and entry "
         "count — nothing to recalculate by hand.", "P"),
        ("4. To submit: open “Summary & Report”, then File ▸ Print ▸ Current sheet, and "
         "save as PDF. It prints the totals cover page plus the itemized list.", "P"),
        ("Credits are self-reported (not accredited). RCPSC Section 2 (Self-Learning) is "
         "self-reported at the physician's discretion.", "NOTE"),
    ]
    values = [[b[0]] for b in blocks]
    ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
    # Formatting per row
    for i, (_, kind) in enumerate(blocks, 1):
        rng = f"A{i}"
        if kind == "TITLE":
            ws.format(rng, _cell(bold=True, size=18, color=NAVY))
        elif kind == "SUB":
            ws.format(rng, _cell(italic=True, size=12, color=GREY))
        elif kind == "H":
            ws.format(rng, _cell(bold=True, size=14, color=NAVY))
        elif kind == "NOTE":
            ws.format(rng, _cell(italic=True, size=12, color=GREY, wrap="WRAP"))
        else:
            ws.format(rng, _cell(size=12, wrap="WRAP", valign="TOP"))
    _set_col_widths(sh, ws, {0: 900})
    return ws


def ensure_activity(sh):
    """Ensure the Activity Log exists with the new headers + formatting."""
    ws = _get_or_add_ws(sh, ACTIVITY_SHEET, rows=400, cols=6)
    existing = ws.row_values(1)
    if existing[:6] != ACTIVITY_HEADERS:
        ws.update(range_name="A1", values=[ACTIVITY_HEADERS],
                  value_input_option="USER_ENTERED")
    format_activity(sh, ws)
    return ws


def format_activity(sh, ws):
    ws.freeze(rows=1)
    ws.format("A1:F1", _cell(bold=True, size=12, color=WHITE, bg=NAVY,
                             align="CENTER", valign="MIDDLE"))
    ws.format(f"A2:F{_LOG_MAXROW}", _cell(size=12, valign="TOP"))
    ws.format(f"A2:C{_LOG_MAXROW}", {"horizontalAlignment": "CENTER"})
    ws.format(f"D2:D{_LOG_MAXROW}", {"wrapStrategy": "WRAP"})
    ws.format(f"F2:F{_LOG_MAXROW}", {"wrapStrategy": "WRAP"})
    _set_col_widths(sh, ws, {0: 96, 1: 70, 2: 160, 3: 380, 4: 150, 5: 540})


# ── Logging operations ───────────────────────────────────────────────────────

def _next_row(ws) -> int:
    return len(ws.get_all_values()) + 1


def log_digest(selected: list, email_date: str) -> str:
    """APPEND one Activity Log row per featured article. Dedup by date."""
    sh = connect()
    ws = ensure_activity(sh)

    if email_date in set(ws.col_values(1)):
        logger.info(f"Activity Log already has {email_date} — skip (no double-log)")
        refresh_report(sh)
        return sh.url

    start = _next_row(ws)
    rows = []
    for i, art in enumerate(selected):
        r = start + i
        row = _activity_row(art, email_date)
        row[2] = f'=IF(B{r}="","",B{r}*2)'   # Credits (self-reported) = Hours × 2
        rows.append(row)
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(f"Appended {len(rows)} rows to '{ACTIVITY_SHEET}' for {email_date}")
    format_activity(sh, ws)
    refresh_report(sh)
    return sh.url


def log_cme(date: str, num_questions: int,
            activity: str = "Weekly self-assessment") -> str:
    """APPEND one self-assessment row to the Activity Log. Dedup by date+marker.

    Self-reportable under RCPSC MOC Section 2 (Self-Learning) at the physician's
    discretion (Section 2 = 2 credits/hr).
    """
    sh = connect()
    ws = ensure_activity(sh)

    title = f"{activity} — {num_questions} questions"
    existing = ws.get_all_values()
    for row in existing[1:]:
        if len(row) >= 4 and row[0] == date and row[3] == title:
            logger.info(f"Activity Log already has self-assessment for {date} — skip")
            refresh_report(sh)
            return sh.url

    r = _next_row(ws)
    row = [date, "", f'=IF(B{r}="","",B{r}*2)', title, "Multiple journals",
           "Self-directed reading & reflection of the week's featured articles."]
    ws.append_rows([row], value_input_option="USER_ENTERED")
    logger.info(f"Appended self-assessment row for {date}")
    format_activity(sh, ws)
    refresh_report(sh)
    return sh.url


# ── Summary & Report ─────────────────────────────────────────────────────────

def refresh_report(sh=None) -> str:
    """Rebuild the print-ready 'Summary & Report' tab: banner + live totals, then
    an itemized table mirroring every Activity Log entry. Returns the URL."""
    from datetime import datetime
    if sh is None:
        sh = connect()
    ensure_activity(sh)

    ws = _get_or_add_ws(sh, REPORT_SHEET, rows=400, cols=6)
    ws.clear()
    _unmerge_all(sh, ws)

    A = f"'{ACTIVITY_SHEET}'"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    DETAIL_START = 12

    # Totals AND the itemized list count ONLY rows where Hours > 0. An article with
    # Hours of 0 (or blank) is excluded from the totals and hidden from the printed
    # list. Open-ended ranges + live formulas → updates the moment Hours changes.
    hrng = f"{A}!B2:B"      # Hours
    crng = f"{A}!C2:C"      # Credits
    rows = [
        ["PRINT THIS PAGE FOR YOUR MOC SUBMISSION  →  File ▸ Print", "", "", "", ""],   # 1
        ["", "", "", "", ""],                                                           # 2
        ["Self-Assessment Tracker — Summary & Report", "", "", "", ""],                 # 3
        [f"Last updated: {stamp}", "", "", "", ""],                                     # 4
        ["", "", "", "", ""],                                                           # 5
        ["Total entries logged", f'=COUNTIF({hrng},">0")', "", "", ""],                 # 6
        ["Total hours", f'=SUMIF({hrng},">0")', "", "", ""],                            # 7
        ["Total credits (self-reported)", f'=SUMIF({hrng},">0",{crng})', "", "", ""],   # 8
        ["", "", "", "", ""],                                                           # 9
        ["Itemized activities (only entries with hours > 0)", "", "", "", ""],          # 10
        ["Date", "Title", "Journal", "Hours", "Credits"],                              # 11
    ]
    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")

    # One spilling FILTER drives the whole detail list. Columns are reordered to
    # Date | Title | Journal | Hours | Credits, and only Hours>0 rows pass through.
    cols = ",".join(f"{A}!{c}2:{c}" for c in ("A", "D", "E", "B", "C"))
    detail = (f'=IFERROR(FILTER({{{cols}}},{hrng}>0),'
              f'"No activities with hours > 0 yet.")')
    ws.update(range_name=f"A{DETAIL_START}", values=[[detail]],
              value_input_option="USER_ENTERED")

    # Formatting
    ws.merge_cells("A1:E1", merge_type="MERGE_ALL")
    ws.format("A1:E1", _cell(bold=True, size=14, color=WHITE, bg=REDCTA,
                             align="CENTER", valign="MIDDLE"))
    ws.merge_cells("A3:E3", merge_type="MERGE_ALL")
    ws.format("A3", _cell(bold=True, size=14, color=NAVY))
    ws.format("A4", _cell(italic=True, size=12, color=GREY))
    ws.format("A6:A8", _cell(bold=True, size=12))
    ws.format("B6:B8", _cell(bold=True, size=12, color=NAVY, align="CENTER"))
    ws.format("A10", _cell(bold=True, size=12, color=NAVY))
    ws.format("A11:E11", _cell(bold=True, size=12, color=WHITE, bg=NAVY, align="CENTER"))
    # Style a generous block (within the grid) so spilled rows are formatted too.
    end = ws.row_count
    ws.format(f"A{DETAIL_START}:E{end}", _cell(size=12, valign="TOP"))
    ws.format(f"B{DETAIL_START}:B{end}", {"wrapStrategy": "WRAP"})
    ws.format(f"A{DETAIL_START}:A{end}", {"horizontalAlignment": "CENTER"})
    ws.format(f"D{DETAIL_START}:E{end}", {"horizontalAlignment": "CENTER"})
    _set_col_widths(sh, ws, {0: 120, 1: 380, 2: 150, 3: 80, 4: 110})

    # Note: Google Sheets has no persistent print-area via API; the report lives on
    # one contiguous tab, so File ▸ Print ▸ "Current sheet" captures all of it.
    logger.info(f"Rebuilt '{REPORT_SHEET}' (dynamic Hours>0 filter)")

    # Also refresh this specialty's Journal Reference tab and the shared,
    # cross-specialty Summary tab. Non-fatal if they fail.
    try:
        ensure_journal_reference(sh)
    except Exception as e:
        logger.warning(f"Journal Reference tab refresh failed: {e}")
    try:
        refresh_cross_summary(sh)
    except Exception as e:
        logger.warning(f"Cross-specialty Summary tab refresh failed: {e}")
    return sh.url


# ── Journal Reference (one tab per specialty) ────────────────────────────────

# Date | Hours | Credits | Title | Journal | APA → see ACTIVITY_HEADERS above.
JOURNAL_REF_HEADERS = ["Journal", "Abbr.", "ISSN", "Impact Factor",
                       "Publisher", "Open Access", "Tier", "Website"]


def ensure_journal_reference(sh) -> str:
    """Create/refresh this specialty's Journal Reference tab with the journal
    metadata from the active specialty config (config.JOURNALS)."""
    ws = _get_or_add_ws(sh, JOURNALS_SHEET, rows=60, cols=len(JOURNAL_REF_HEADERS))
    ws.clear()
    _unmerge_all(sh, ws)

    title = f"{config.SPECIALTY_NAME} — Journal Reference"
    rows = [[title] + [""] * (len(JOURNAL_REF_HEADERS) - 1), JOURNAL_REF_HEADERS]
    for j in config.JOURNALS:
        fully_oa = j.get("fully_open_access")
        oa = "Fully OA" if fully_oa else "Hybrid / subscription"
        tier = "Tier 2 (general — filtered)" if j.get("filter_required") \
            else "Tier 1 (dedicated)"
        rows.append([
            j.get("name", ""), j.get("abbreviation", ""), j.get("issn", ""),
            j.get("impact_factor", ""), j.get("publisher", ""), oa, tier,
            j.get("website", ""),
        ])
    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")

    ncols = len(JOURNAL_REF_HEADERS)
    last_col = chr(ord("A") + ncols - 1)
    ws.merge_cells(f"A1:{last_col}1", merge_type="MERGE_ALL")
    ws.format("A1", _cell(bold=True, size=14, color=NAVY))
    ws.format(f"A2:{last_col}2", _cell(bold=True, size=12, color=WHITE, bg=NAVY,
                                       align="CENTER", valign="MIDDLE"))
    ws.freeze(rows=2)
    _set_col_widths(sh, ws, {0: 320, 1: 110, 2: 100, 3: 110, 4: 110, 5: 150,
                             6: 200, 7: 300})
    logger.info(f"Rebuilt '{JOURNALS_SHEET}' ({len(config.JOURNALS)} journals)")
    return sh.url


# ── Cross-specialty Summary (one shared tab) ─────────────────────────────────

def refresh_cross_summary(sh=None) -> str:
    """Rebuild the shared 'Summary' tab that aggregates self-reported hours and
    credits across EVERY specialty's MOC log tab. Only specialties whose log tab
    already exists in this spreadsheet are included (so a not-yet-run specialty
    never produces a #REF error)."""
    if sh is None:
        sh = connect()

    existing = {ws.title for ws in sh.worksheets()}
    specialties = [s for s in config.ALL_SPECIALTIES if s["log_tab"] in existing]

    ws = _get_or_add_ws(sh, CROSS_SUMMARY_SHEET, rows=60, cols=5)
    ws.clear()
    _unmerge_all(sh, ws)

    header = [
        ["Journal Digest — Cross-Specialty Summary", "", "", "", ""],
        ["Self-reported under RCPSC MOC Section 2 (Self-Learning); not accredited.",
         "", "", "", ""],
        ["", "", "", "", ""],
        ["Specialty", "Entries (hrs > 0)", "Total hours",
         "Total credits (self-reported)", "MOC log tab"],
    ]
    rows = list(header)
    data_start = len(header) + 1   # 1-based row of first specialty
    for s in specialties:
        tab = s["log_tab"]
        a = f"'{tab}'"
        hrng = f"{a}!B2:B"
        crng = f"{a}!C2:C"
        rows.append([
            s["name"],
            f'=COUNTIF({hrng},">0")',
            f'=SUMIF({hrng},">0")',
            f'=SUMIF({hrng},">0",{crng})',
            tab,
        ])
    # Grand totals row (sums the per-specialty rows above).
    total_row = data_start + len(specialties)
    if specialties:
        rows.append([
            "ALL SPECIALTIES",
            f"=SUM(B{data_start}:B{total_row - 1})",
            f"=SUM(C{data_start}:C{total_row - 1})",
            f"=SUM(D{data_start}:D{total_row - 1})",
            "",
        ])
    else:
        rows.append(["(no specialty MOC logs yet)", "", "", "", ""])
    ws.update(range_name="A1", values=rows, value_input_option="USER_ENTERED")

    ws.merge_cells("A1:E1", merge_type="MERGE_ALL")
    ws.format("A1", _cell(bold=True, size=16, color=NAVY))
    ws.merge_cells("A2:E2", merge_type="MERGE_ALL")
    ws.format("A2", _cell(italic=True, size=11, color=GREY))
    ws.format("A4:E4", _cell(bold=True, size=12, color=WHITE, bg=NAVY,
                             align="CENTER", valign="MIDDLE"))
    ws.format(f"A{total_row}:E{total_row}", _cell(bold=True, size=12, color=NAVY,
                                                  bg=LIGHT))
    ws.format(f"B{data_start}:D{total_row}", {"horizontalAlignment": "CENTER"})
    ws.freeze(rows=4)
    _set_col_widths(sh, ws, {0: 220, 1: 150, 2: 120, 3: 220, 4: 200})
    logger.info(f"Rebuilt '{CROSS_SUMMARY_SHEET}' "
                f"({len(specialties)} specialty log tab(s))")
    return sh.url


# Back-compat aliases for existing callers.
def refresh_summary() -> str:
    return refresh_report()


def spreadsheet_url() -> str:
    return connect().url
