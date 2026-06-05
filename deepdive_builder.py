"""
Anesthesia Journal Digest — Deep Dive Page Builder
===================================================
Renders the weekly "Deep Dive" GitHub Pages page: one expandable card per
featured article (first expanded, the rest collapsed), each with a bottom line
and six sections. Also builds the dated index that lists past Deep Dive pages.

Pure rendering — no network, no API. Input is the list of summary dicts from
summary_generator.generate_summaries().
"""

from __future__ import annotations

import html
from datetime import datetime

# The five standard sections (Limitations is rendered separately, amber-tinted).
_SECTIONS = [
    ("🔬", "What they did", "what_they_did"),
    ("📐", "Design", "design"),
    ("📊", "What they found", "what_they_found"),
    ("💡", "What it means", "what_it_means"),
    ("⭐", "Why it matters", "why_it_matters"),
]

_CSS = """
  :root{
    --ink:#1a5276; --ink2:#2e86c1; --text:#2c3e50; --muted:#7b8794;
    --line:#e3e8ee; --bg:#f5f6fa; --card:#ffffff;
    --warn:#b9770e; --warnbg:#fff8ec; --warnline:#f0d9a8;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);font-family:Arial,Helvetica,sans-serif;color:var(--text);line-height:1.55;}
  .page{max-width:760px;margin:0 auto;padding:28px 18px 60px;}
  .page-head{padding:6px 4px 18px;}
  .page-head h1{font-size:22px;color:var(--ink);margin:0 0 4px;}
  .page-head .sub{font-size:13px;color:var(--muted);margin:0;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;
        box-shadow:0 2px 10px rgba(0,0,0,.05);overflow:hidden;margin:18px 0;}
  .hdr{padding:18px 20px 16px;}
  .tags{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:9px;}
  .badge{font-size:11px;font-weight:bold;border-radius:4px;padding:2px 8px;letter-spacing:.02em;}
  .b-journal{background:var(--ink);color:#fff;}
  .b-if{background:#eef3f8;color:var(--ink);}
  .b-oa{background:#27ae60;color:#fff;}
  .title{font-size:18px;font-weight:bold;color:var(--text);margin:2px 0 4px;line-height:1.35;}
  .meta{font-size:12px;color:var(--muted);}
  .bottomline{margin:14px 0 2px;padding:12px 14px;background:#eef6fb;
              border-left:4px solid var(--ink2);border-radius:0 8px 8px 0;}
  .bottomline .lab{font-size:11px;font-weight:bold;text-transform:uppercase;
                   letter-spacing:.06em;color:var(--ink);}
  .bottomline p{margin:4px 0 0;font-size:14px;color:#33444f;}
  .toggle{display:inline-flex;align-items:center;gap:8px;margin-top:14px;
          background:var(--ink);color:#fff;border:none;cursor:pointer;
          font-family:inherit;font-size:14px;font-weight:bold;
          padding:10px 18px;border-radius:22px;}
  .toggle .chev{transition:transform .2s ease;font-size:12px;}
  .card.open .toggle .chev{transform:rotate(180deg);}
  .body{display:none;border-top:1px solid var(--line);padding:6px 20px 20px;}
  .card.open .body{display:block;}
  .sec{padding:15px 0 4px;border-bottom:1px solid #f0f2f5;}
  .sec:last-child{border-bottom:none;}
  .sec h3{display:flex;align-items:center;gap:9px;margin:0 0 6px;
          font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink);}
  .sec h3 .ic{width:22px;height:22px;border-radius:6px;background:#eef3f8;
              display:inline-flex;align-items:center;justify-content:center;font-size:13px;}
  .sec p{margin:0;font-size:14px;color:#3a4750;}
  .sec.caution{background:var(--warnbg);border:1px solid var(--warnline);
               border-radius:10px;padding:14px 16px;margin-top:14px;}
  .sec.caution h3{color:var(--warn);}
  .sec.caution h3 .ic{background:#fbecca;}
  .sec.caution p{color:#7a5a17;}
  .links{margin-top:16px;display:flex;flex-wrap:wrap;gap:10px;}
  .links a{font-size:13px;font-weight:bold;text-decoration:none;color:var(--ink2);
           border:1px solid var(--line);border-radius:8px;padding:8px 14px;}
  .src-note{margin-top:14px;font-size:11px;color:var(--muted);font-style:italic;}
  .foot{text-align:center;font-size:11px;color:var(--muted);margin-top:30px;line-height:1.5;}
  .foot a{color:var(--ink2);text-decoration:none;}
"""

_TOGGLE_JS = """
  function toggleCard(id){
    var c = document.getElementById(id);
    var open = c.classList.toggle('open');
    c.querySelector('.lab-text').textContent = open ? 'Hide summary' : 'Read full summary';
  }
"""


def _card(s: dict, idx: int, is_open: bool) -> str:
    e = html.escape
    anchor = s.get("anchor") or f"a{idx}"

    tags = f'<span class="badge b-journal">{e(str(s.get("journal_abbr", "")))}</span>'
    if s.get("impact_factor") not in (None, ""):
        tags += f'<span class="badge b-if">IF {e(str(s["impact_factor"]))}</span>'
    if s.get("is_open_access"):
        tags += '<span class="badge b-oa">OPEN ACCESS</span>'

    meta = " · ".join(b for b in (e(str(s.get("authors", ""))),
                                  e(str(s.get("journal_abbr", ""))),
                                  e(str(s.get("date_str", "")))) if b)

    secs = ""
    for icon, label, key in _SECTIONS:
        val = s.get(key)
        if val:
            secs += (f'<div class="sec"><h3><span class="ic">{icon}</span> {label}</h3>'
                     f'<p>{e(val)}</p></div>')
    if s.get("limitations"):
        secs += ('<div class="sec caution"><h3><span class="ic">⚠️</span> Limitations</h3>'
                 f'<p>{e(s["limitations"])}</p></div>')

    links = ""
    if s.get("url"):
        links += (f'<a href="{e(s["url"])}" target="_blank" rel="noopener">'
                  'Read the full article ↗</a>')

    src = ("Summary generated from the open-access full text and grounded in the "
           "reported results."
           if s.get("source") == "fulltext"
           else "Summary generated from the abstract (full text not open access).")
    src += " AI-generated; verify against the source before clinical use."

    open_cls = " open" if is_open else ""
    label_txt = "Hide summary" if is_open else "Read full summary"

    return f"""  <div class="card{open_cls}" id="{anchor}">
    <div class="hdr">
      <div class="tags">{tags}</div>
      <div class="title">{e(str(s.get("title", "")))}</div>
      <div class="meta">{meta}</div>
      <div class="bottomline">
        <span class="lab">Bottom line</span>
        <p>{e(str(s.get("bottom_line", "")))}</p>
      </div>
      <button class="toggle" onclick="toggleCard('{anchor}')">
        <span class="lab-text">{label_txt}</span> <span class="chev">▾</span>
      </button>
    </div>
    <div class="body">
{secs}
      <div class="links">{links}</div>
      <div class="src-note">{src}</div>
    </div>
  </div>"""


def build_deepdive_page(summaries: list, date_obj: datetime | None = None) -> str:
    """Build the full Deep Dive page (first card expanded, rest collapsed)."""
    date_obj = date_obj or datetime.now()
    pretty = date_obj.strftime("%A, %B %d, %Y")
    n = len(summaries)
    cards = "\n".join(_card(s, i, is_open=(i == 1))
                      for i, s in enumerate(summaries, 1))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Deep Dive · {pretty}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <div class="page-head">
    <h1>📖 Anesthesia Digest — Deep Dive</h1>
    <p class="sub">{pretty} · structured summaries of this week's {n} featured articles</p>
  </div>
{cards}
  <div class="foot">
    Anesthesia Journal Digest · summaries generated by AI (Claude) from the article
    source text.<br><a href="index.html">← All Deep Dive editions</a>
  </div>
</div>
<script>{_TOGGLE_JS}</script>
</body>
</html>"""


def build_deepdive_index(pages: list) -> str:
    """Build the index listing all dated Deep Dive pages (newest first).

    pages: list of (YYYY-MM-DD, filename) tuples.
    """
    items = ""
    for date_part, filename in pages:
        try:
            label = datetime.strptime(date_part, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            label = date_part
        items += f'    <li><a href="{html.escape(filename)}">{label}</a></li>\n'
    if not items:
        items = '    <li style="color:#7b8794;list-style:none;">No editions yet.</li>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Deep Dive Archive</title>
<style>{_CSS}
  .archive ul{{list-style:none;padding:0;margin:14px 0 0;}}
  .archive li{{padding:0;margin:10px 0;}}
  .archive a{{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;
             padding:14px 16px;font-size:15px;font-weight:600;color:var(--ink);text-decoration:none;}}
  .archive a:hover{{border-color:var(--ink2);}}
</style>
</head>
<body>
<div class="page">
  <div class="page-head">
    <h1>📖 Deep Dive — Archive</h1>
    <p class="sub">Weekly structured summaries of the featured anesthesia articles</p>
  </div>
  <div class="archive">
  <ul>
{items}  </ul>
  </div>
</div>
</body>
</html>"""
