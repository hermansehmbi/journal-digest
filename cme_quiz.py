"""
Anesthesia Journal Digest — Interactive CME Quiz Page Builder
==============================================================
Builds a self-contained, mobile-friendly HTML quiz page from the week's CME
questions. The page:
  - shows all questions with selectable options (large, readable type),
  - reveals nothing until the user clicks "Submit Test",
  - on submit, grades the test, shows the score, and reveals the correct answer
    + a full-paragraph explanation under each question,
  - takes the participant's full name + credentials, then builds a classical,
    formal PDF certificate with jsPDF (CDN): timestamped, with a unique
    certificate ID and an auditable verification code.

Also builds docs/cme/index.html (quiz listing) and docs/cme/cert-preview.html
(a standalone certificate-design preview with sample data).

Everything runs client-side and is served statically from GitHub Pages.

Templates use %%TOKEN%% placeholders filled by str.replace (NOT str.format), so
the embedded CSS/JS braces are left untouched.
"""

import json
import html
from datetime import datetime


def build_quiz_page(questions: list[dict], date_obj: datetime) -> str:
    """Return a complete interactive HTML quiz page for the given questions."""
    pretty_date = date_obj.strftime("%B %d, %Y")
    iso_date = date_obj.strftime("%Y-%m-%d")
    total = len(questions)

    # Only what the client grader needs (correct letter + explanation + source).
    quiz_data = []
    for q in questions:
        quiz_data.append({
            "correct": q.get("correct", ""),
            "rationale": q.get("rationale", ""),
            "source_article": q.get("source_article", ""),
            "source_journal": q.get("source_journal", ""),
            "source_url": q.get("source_url", ""),
        })
    quiz_json = json.dumps(quiz_data)

    # Static question markup (no answers exposed in the visible DOM).
    questions_html = ""
    for i, q in enumerate(questions):
        opts_html = ""
        for letter in ("A", "B", "C", "D"):
            text = q.get("options", {}).get(letter, "")
            if not text:
                continue
            opts_html += f"""
        <label class="option" for="q{i}{letter}">
          <input type="radio" id="q{i}{letter}" name="q{i}" value="{letter}">
          <span class="opt-letter">{letter}</span>
          <span class="opt-text">{html.escape(text)}</span>
        </label>"""

        questions_html += f"""
    <section class="question" id="card{i}" data-index="{i}">
      <div class="qnum">Question {i + 1} <span class="of">of {total}</span></div>
      <div class="qtext">{html.escape(q.get("question", ""))}</div>
      <div class="options">{opts_html}
      </div>
      <div class="result" id="result{i}" hidden></div>
    </section>"""

    page = _PAGE_TEMPLATE
    page = page.replace("%%PRETTY_DATE%%", pretty_date)
    page = page.replace("%%ISO_DATE%%", iso_date)
    page = page.replace("%%TOTAL%%", str(total))
    page = page.replace("%%QUESTIONS_HTML%%", questions_html)
    page = page.replace("%%QUIZ_JSON%%", quiz_json)
    page = page.replace("%%CERT_JS%%", _CERT_JS)
    return page


def build_quiz_index(quizzes: list[tuple[str, str]]) -> str:
    """Build docs/cme/index.html listing recent quizzes (newest first)."""
    items = ""
    for iso_date, filename in quizzes:
        try:
            label = datetime.strptime(iso_date, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            label = iso_date
        items += (
            f'      <li><a href="{html.escape(filename)}">'
            f'Weekly Self-Assessment — {label}</a></li>\n'
        )
    if not items:
        items = '      <li class="empty">No quizzes published yet.</li>\n'
    return _INDEX_TEMPLATE.replace("%%ITEMS%%", items)


def build_cert_preview() -> str:
    """Standalone page that renders the certificate with sample data on click."""
    return _CERT_PREVIEW_TEMPLATE.replace("%%CERT_JS%%", _CERT_JS)


# ── Shared certificate JavaScript (classical / formal design) ────────────────
# Pure client-side. Drawn with jsPDF primitives. Used by both the quiz page and
# the certificate preview page so the design stays in one place.

_CERT_JS = r"""
function ad2(n){ return ('0' + n).slice(-2); }

// FNV-1a 32-bit hash -> short uppercase base36 code (deterministic & auditable).
function adHash(str){
  var h = 0x811c9dc5 >>> 0;
  for (var i = 0; i < str.length; i++){
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return ('0000000' + h.toString(36).toUpperCase()).slice(-7);
}

function makeCertId(d){
  var rnd = Math.floor(Math.random() * 0x10000).toString(16).toUpperCase();
  rnd = ('000' + rnd).slice(-4);
  return 'AD-' + d.getFullYear() + ad2(d.getMonth() + 1) + ad2(d.getDate()) + '-' + rnd;
}

function adVerCode(name, ymd, score, total, certId){
  return adHash(name + '|' + ymd + '|' + score + '/' + total + '|' + certId);
}

function drawCorners(doc, x1, y1, x2, y2, color){
  doc.setDrawColor(color[0], color[1], color[2]);
  doc.setLineWidth(1);
  var s = 26, o = 44;
  var pts = [[x1+o,y1+o,1,1],[x2-o,y1+o,-1,1],[x1+o,y2-o,1,-1],[x2-o,y2-o,-1,-1]];
  for (var i = 0; i < pts.length; i++){
    var px = pts[i][0], py = pts[i][1], dx = pts[i][2], dy = pts[i][3];
    doc.line(px, py, px + dx * s, py);
    doc.line(px, py, px, py + dy * s);
    doc.circle(px, py, 2.2, 'F');
    doc.circle(px + dx * s, py, 1.4, 'F');
    doc.circle(px, py + dy * s, 1.4, 'F');
  }
}

function drawSeal(doc, cx, cy, gold, wax){
  // Rosette of petals, then a stamped disc with initials.
  doc.setFillColor(wax[0], wax[1], wax[2]);
  var R = 30;
  for (var a = 0; a < 360; a += 24){
    var r = a * Math.PI / 180;
    var x1 = cx + Math.cos(r) * R,            y1 = cy + Math.sin(r) * R;
    var x2 = cx + Math.cos(r + 0.27) * (R*0.66), y2 = cy + Math.sin(r + 0.27) * (R*0.66);
    var x3 = cx + Math.cos(r - 0.27) * (R*0.66), y3 = cy + Math.sin(r - 0.27) * (R*0.66);
    doc.triangle(x1, y1, x2, y2, x3, y3, 'F');
  }
  doc.setFillColor(wax[0], wax[1], wax[2]);
  doc.circle(cx, cy, R * 0.72, 'F');
  doc.setDrawColor(gold[0], gold[1], gold[2]);
  doc.setLineWidth(1.3); doc.circle(cx, cy, R * 0.72, 'S');
  doc.setLineWidth(0.7); doc.circle(cx, cy, R * 0.55, 'S');
  doc.setFont('times', 'bold'); doc.setFontSize(12); doc.setTextColor(255, 246, 236);
  doc.text('AD', cx, cy - 1, {align:'center'});
  doc.setFontSize(5); doc.text('SELF-ASSESSMENT', cx, cy + 9, {align:'center'});
}

// opts: {name, score, total, completedAt(Date), certId?, verCode?}
// Returns {certId, verCode}. Builds and downloads the PDF.
function generateCertificate(opts){
  var ns = window.jspdf || {};
  var JsPDF = ns.jsPDF;
  if (!JsPDF){ alert('Certificate library failed to load. Check your internet connection and try again.'); return null; }

  var name  = (opts.name || '').trim();
  var score = opts.score, total = opts.total;
  var now   = opts.completedAt || new Date();
  var certId = opts.certId || makeCertId(now);
  var ymd = now.getFullYear() + '-' + ad2(now.getMonth() + 1) + '-' + ad2(now.getDate());
  var pct = Math.round(score / total * 100);
  var ver = opts.verCode || adVerCode(name, ymd, score, total, certId);
  var dateStr = now.toLocaleDateString(undefined, {year:'numeric', month:'long', day:'numeric'});
  var timeStr = now.toLocaleTimeString(undefined, {hour:'numeric', minute:'2-digit'});
  var stamp = dateStr + ' at ' + timeStr;
  var weekOf = (opts.weekOf || dateStr);   // the literature week this self-assessment covers

  var doc = new JsPDF({orientation:'landscape', unit:'pt', format:'letter'});
  var W = doc.internal.pageSize.getWidth();
  var H = doc.internal.pageSize.getHeight();
  var navy = [26,82,118], gold = [176,141,87], ink = [44,62,80], wax = [140,32,32];

  // Parchment background
  doc.setFillColor(252, 250, 244); doc.rect(0, 0, W, H, 'F');

  // Decorative double border
  doc.setDrawColor(navy[0], navy[1], navy[2]); doc.setLineWidth(4);
  doc.rect(24, 24, W - 48, H - 48);
  doc.setDrawColor(gold[0], gold[1], gold[2]); doc.setLineWidth(1.4);
  doc.rect(34, 34, W - 68, H - 68);
  drawCorners(doc, 24, 24, W - 24, H - 24, gold);

  // Title + flourish
  doc.setFont('times', 'bold'); doc.setFontSize(34); doc.setTextColor(navy[0], navy[1], navy[2]);
  doc.text('Certificate of Completion', W / 2, 108, {align:'center'});
  doc.setDrawColor(gold[0], gold[1], gold[2]); doc.setLineWidth(1);
  doc.line(W / 2 - 160, 122, W / 2 + 160, 122);
  doc.circle(W / 2 - 168, 122, 2, 'F'); doc.circle(W / 2 + 168, 122, 2, 'F');

  doc.setFont('times', 'italic'); doc.setFontSize(16); doc.setTextColor(ink[0], ink[1], ink[2]);
  doc.text('Self-Assessment Activity', W / 2, 148, {align:'center'});

  doc.setFont('times', 'normal'); doc.setFontSize(13);
  doc.text('This certifies that', W / 2, 192, {align:'center'});

  doc.setFont('times', 'bold'); doc.setFontSize(26); doc.setTextColor(navy[0], navy[1], navy[2]);
  doc.text(name || '—', W / 2, 226, {align:'center'});
  var nameW = Math.min(460, Math.max(220, doc.getTextWidth(name || '—') + 70));
  doc.setDrawColor(gold[0], gold[1], gold[2]); doc.setLineWidth(0.8);
  doc.line(W / 2 - nameW / 2, 236, W / 2 + nameW / 2, 236);

  doc.setFont('times', 'normal'); doc.setFontSize(13); doc.setTextColor(ink[0], ink[1], ink[2]);
  doc.text('completed a self-assessment based on the week of ' + weekOf, W / 2, 266, {align:'center'});
  doc.text('anesthesia literature (' + total + ' questions), with a score of', W / 2, 284, {align:'center'});

  doc.setFont('times', 'bold'); doc.setFontSize(20); doc.setTextColor(30, 110, 70);
  doc.text(score + ' / ' + total + '    (' + pct + '%)', W / 2, 314, {align:'center'});

  doc.setFont('times', 'normal'); doc.setFontSize(12); doc.setTextColor(ink[0], ink[1], ink[2]);
  doc.text('Completed: ' + stamp, W / 2, 340, {align:'center'});

  drawSeal(doc, W / 2, 384, gold, wax);

  // Bottom block — everything sits clearly INSIDE the inner border (which is at
  // H-34) with margin. Disclaimer is drawn line-by-line, then the ID row, so
  // nothing overruns or hides behind the frame.
  doc.setFont('times', 'italic'); doc.setFontSize(8.5); doc.setTextColor(110, 110, 110);
  var disc = 'This is a self-assessment completion record. Time spent reading and reflecting on the literature may be eligible for self-reporting under RCPSC MOC Section 2 (Self-Learning); confirm your own eligibility.';
  var lines = doc.splitTextToSize(disc, W - 160);
  var lh = 11;                          // line spacing (pt)
  var discLast = H - 74;                // baseline of the LAST disclaimer line
  var discFirst = discLast - (lines.length - 1) * lh;
  for (var li = 0; li < lines.length; li++){
    doc.text(lines[li], W / 2, discFirst + li * lh, {align:'center'});
  }

  // Certificate ID (left) + verification code (right) on one row, above the frame.
  doc.setFont('courier', 'normal'); doc.setFontSize(9.5); doc.setTextColor(ink[0], ink[1], ink[2]);
  doc.text('Certificate ID:  ' + certId, 72, H - 50);
  doc.text('Verification:  ' + ver, W - 72, H - 50, {align:'right'});

  doc.save('Certificate_of_Completion_' + certId + '.pdf');
  return {certId: certId, verCode: ver};
}
"""


# ── Page templates ───────────────────────────────────────────────────────────

_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Weekly Review & Self-Assessment (%%PRETTY_DATE%%)</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<style>
  :root { --navy:#1a5276; --blue:#2e86c1; --green:#1e8449; --red:#c0392b;
          --bg:#f4f6f9; --card:#ffffff; --line:#e3e8ee; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:var(--bg); color:#2c3e50; font-size:16px; line-height:1.6; }
  header { background:linear-gradient(135deg,var(--navy),var(--blue)); color:#fff;
           padding:28px 20px; text-align:center; }
  header h1 { margin:0; font-size:25px; font-family:Georgia,serif; }
  header p { margin:8px 0 0; font-size:15px; opacity:.92; }
  main { max-width:780px; margin:0 auto; padding:18px 16px 70px; }
  .intro { background:#eef5fb; border:1px solid #d6e6f5; border-radius:10px;
           padding:16px 18px; font-size:15px; color:#34516b; margin-bottom:20px; }
  .question { background:var(--card); border:1px solid var(--line); border-radius:12px;
             padding:20px 20px 18px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
  .qnum { font-size:13px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
          color:var(--blue); }
  .qnum .of { color:#9bb1c4; font-weight:600; }
  .qtext { font-size:18px; font-weight:600; margin:8px 0 16px; line-height:1.5; }
  .option { display:flex; align-items:flex-start; gap:12px; padding:13px 14px;
            border:1px solid var(--line); border-radius:9px; margin:9px 0; cursor:pointer;
            transition:background .12s,border-color .12s; }
  .option:hover { background:#f7fafd; }
  .option input { margin-top:4px; flex:none; width:18px; height:18px; }
  .opt-letter { font-weight:700; color:var(--navy); flex:none; font-size:16px; }
  .opt-text { font-size:16px; }
  .option.correct { border-color:var(--green); background:#eafaf1; }
  .option.incorrect { border-color:var(--red); background:#fdedec; }
  .result { margin-top:14px; padding:14px 16px; border-radius:9px; font-size:15.5px;
            line-height:1.65; }
  .result.right { background:#eafaf1; border:1px solid #abe2c1; }
  .result.wrong { background:#fdedec; border:1px solid #f3b7b1; }
  .result .verdict { font-weight:700; display:block; margin-bottom:6px; font-size:16px; }
  .result .src { display:block; margin-top:10px; font-size:13.5px; color:#5b6b7a; }
  .result .src a { color:var(--blue); }
  .actions { position:sticky; bottom:0; background:linear-gradient(180deg,rgba(244,246,249,0),var(--bg) 32%);
             padding:18px 0 10px; text-align:center; }
  button { font:inherit; border:none; border-radius:26px; padding:15px 30px; font-weight:700;
           font-size:16px; cursor:pointer; }
  .btn-submit { background:var(--blue); color:#fff; }
  .btn-submit:disabled { background:#9bb9d1; cursor:default; }
  .btn-cert { background:var(--green); color:#fff; margin-left:8px; }
  .scorebar { display:none; background:var(--navy); color:#fff; border-radius:12px;
              padding:18px 20px; text-align:center; margin-bottom:20px; }
  .scorebar .pct { font-size:34px; font-weight:800; }
  .scorebar .detail { font-size:15px; opacity:.92; margin-top:2px; }
  .namebox { display:none; margin-top:16px; }
  .namebox label { display:block; font-size:14px; opacity:.92; margin-bottom:7px; }
  .namebox input { font:inherit; font-size:16px; padding:12px 14px; border-radius:8px;
                   border:1px solid var(--line); width:340px; max-width:86%; color:#2c3e50; }
  .certinfo { display:none; margin-top:12px; font-size:13px; opacity:.92; font-family:monospace; }
  .warn { color:var(--red); font-size:15px; margin-top:12px; display:none; }
  footer { text-align:center; font-size:12.5px; color:#9bb1c4; padding:26px 16px 44px; }
  footer a { color:#9bb1c4; }
  @media (max-width:520px) {
    .qtext { font-size:17px; }
    .btn-cert { margin:10px 0 0; }
    .actions button { width:100%; }
    .namebox input { width:100%; max-width:100%; }
  }
</style>
</head>
<body>
  <header>
    <h1>&#127891; Anesthesia Digest — Weekly Review &amp; Self-Assessment</h1>
    <p>%%PRETTY_DATE%% · %%TOTAL%% single-best-answer questions</p>
  </header>
  <main>
    <div class="scorebar" id="scorebar">
      <div class="pct" id="scorePct">0%</div>
      <div class="detail" id="scoreDetail"></div>
      <div class="namebox" id="namebox">
        <label for="participant">Enter your full name &amp; credentials for the certificate</label>
        <input type="text" id="participant" placeholder="e.g. Dr. Jane Smith, MD, FRCPC" autocomplete="name">
      </div>
      <div class="certinfo" id="certinfo"></div>
    </div>

    <div class="intro">
      Answer all the questions, then press <strong>Submit Test</strong> to see your score and the
      explanations. This is a self-assessment tool to support your own learning. Time spent reading
      and reflecting on the literature may be eligible for self-reporting under
      <strong>RCPSC MOC Section&nbsp;2 (Self-Learning)</strong> — confirm your own eligibility.
    </div>

    <form id="quizForm">%%QUESTIONS_HTML%%
    </form>

    <p class="warn" id="warn">Please answer all questions before submitting.</p>

    <div class="actions">
      <button type="button" class="btn-submit" id="submitBtn">Submit Test</button>
      <button type="button" class="btn-cert" id="certBtn" style="display:none;">Download Certificate</button>
    </div>
  </main>
  <footer>
    Anesthesia Journal Digest · AI-generated self-assessment · Not affiliated with any journal.<br>
    <a href="index.html">&#8592; All weekly quizzes</a>
  </footer>

<script>
%%CERT_JS%%

  var QUIZ = %%QUIZ_JSON%%;
  var QUIZ_DATE = "%%PRETTY_DATE%%";
  var TOTAL = %%TOTAL%%;
  var graded = false;
  var lastScore = 0;

  function escapeHtml(s){
    return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  document.getElementById("submitBtn").addEventListener("click", function(){
    var unanswered = 0;
    for (var i = 0; i < TOTAL; i++){
      if (!document.querySelector('input[name="q' + i + '"]:checked')) unanswered++;
    }
    if (unanswered > 0 && !graded){
      document.getElementById("warn").style.display = "block";
      return;
    }
    document.getElementById("warn").style.display = "none";
    grade();
  });

  function grade(){
    var correct = 0;
    for (var i = 0; i < TOTAL; i++){
      var chosen = document.querySelector('input[name="q' + i + '"]:checked');
      var ans = QUIZ[i].correct;
      var picked = chosen ? chosen.value : null;
      if (picked === ans) correct++;

      var labels = document.querySelectorAll('#card' + i + ' .option');
      labels.forEach(function(lab){
        var val = lab.querySelector("input").value;
        lab.querySelector("input").disabled = true;
        if (val === ans) lab.classList.add("correct");
        else if (chosen && val === picked) lab.classList.add("incorrect");
      });

      var res = document.getElementById("result" + i);
      var right = picked === ans;
      var srcHtml = "";
      if (QUIZ[i].source_article){
        var label = (QUIZ[i].source_journal ? QUIZ[i].source_journal + " — " : "") + QUIZ[i].source_article;
        var url = QUIZ[i].source_url || "#";
        srcHtml = '<span class="src">Source: <a href="' + url + '" target="_blank" rel="noopener">' +
                  escapeHtml(label) + '</a></span>';
      }
      res.className = "result " + (right ? "right" : "wrong");
      res.innerHTML = '<span class="verdict">' +
        (right ? "&#10003; Correct" : "&#10007; Your answer: " + (picked || "—") +
                 " · Correct answer: " + ans) + '</span>' +
        escapeHtml(QUIZ[i].rationale) + srcHtml;
      res.hidden = false;
    }

    lastScore = correct;
    graded = true;
    var pct = Math.round((correct / TOTAL) * 100);
    document.getElementById("scorePct").textContent = pct + "%";
    document.getElementById("scoreDetail").textContent = correct + " of " + TOTAL + " correct";
    document.getElementById("scorebar").style.display = "block";
    document.getElementById("namebox").style.display = "block";
    document.getElementById("submitBtn").textContent = "Re-check Answers";
    document.getElementById("certBtn").style.display = "inline-block";
    document.getElementById("scorebar").scrollIntoView({behavior:"smooth", block:"start"});
  }

  document.getElementById("certBtn").addEventListener("click", function(){
    if (!graded) return;
    var name = (document.getElementById("participant").value || "").trim();
    if (!name){
      var inp = document.getElementById("participant");
      inp.style.borderColor = "#ffd27f";
      inp.focus();
      alert("Please enter your full name and credentials before downloading the certificate.");
      return;
    }
    var info = generateCertificate({name:name, score:lastScore, total:TOTAL, completedAt:new Date(), weekOf:QUIZ_DATE});
    if (info){
      var box = document.getElementById("certinfo");
      box.style.display = "block";
      box.textContent = "Certificate ID " + info.certId + "  ·  Verification " + info.verCode;
    }
  });
</script>
</body>
</html>"""


_INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Weekly Review & Self-Assessment</title>
<style>
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:#f4f6f9; color:#2c3e50; font-size:16px; }
  header { background:linear-gradient(135deg,#1a5276,#2e86c1); color:#fff;
           padding:30px 20px; text-align:center; }
  header h1 { margin:0; font-size:25px; font-family:Georgia,serif; }
  header p { margin:8px 0 0; font-size:15px; opacity:.92; }
  main { max-width:660px; margin:0 auto; padding:24px 18px 60px; }
  ul { list-style:none; padding:0; margin:0; }
  li { background:#fff; border:1px solid #e3e8ee; border-radius:10px; margin:11px 0;
       box-shadow:0 1px 3px rgba(0,0,0,.05); }
  li a { display:block; padding:18px 20px; color:#1a5276; text-decoration:none;
         font-weight:600; font-size:17px; }
  li a:hover { background:#f7fafd; }
  li.empty { padding:18px 20px; color:#9bb1c4; font-style:italic; }
  .preview-link { text-align:center; margin-top:18px; font-size:14px; }
  .preview-link a { color:#2e86c1; }
  footer { text-align:center; font-size:12.5px; color:#9bb1c4; padding:26px 16px; }
  footer a { color:#9bb1c4; }
</style>
</head>
<body>
  <header>
    <h1>&#127891; Weekly Self-Assessments</h1>
    <p>Anesthesia Journal Digest · self-assessment to support your own learning</p>
  </header>
  <main>
    <ul>
%%ITEMS%%    </ul>
    <p class="preview-link"><a href="cert-preview.html">Preview the certificate design &#8594;</a></p>
  </main>
  <footer>
    AI-generated self-assessment · Not affiliated with any journal · <a href="../">&#8592; Audio summaries</a>
  </footer>
</body>
</html>"""


_CERT_PREVIEW_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Certificate Preview</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<style>
  body { margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:linear-gradient(160deg,#0e2a3b,#1a5276); color:#eaf2f8;
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }
  .card { background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12);
          border-radius:18px; padding:38px; max-width:560px; text-align:center;
          box-shadow:0 12px 40px rgba(0,0,0,.35); }
  .seal { font-size:56px; margin-bottom:8px; }
  h1 { font-size:24px; margin:0 0 10px; font-family:Georgia,serif; }
  p { font-size:15px; color:#cfe3f2; line-height:1.6; }
  button { font:inherit; font-size:16px; font-weight:700; border:none; border-radius:26px;
           padding:15px 30px; margin-top:18px; cursor:pointer; background:#caa75a; color:#3a2c10; }
  .meta { margin-top:16px; font-size:13px; font-family:monospace; color:#9ecbf0; min-height:18px; }
  .note { margin-top:18px; font-size:12px; color:#8fb3cd; font-style:italic; }
</style>
</head>
<body>
  <main class="card">
    <div class="seal">&#127891;</div>
    <h1>Certificate of Completion — Design Preview</h1>
    <p>Click below to generate a sample certificate PDF (<strong>Dr. Sample, MD, FRCPC</strong>,
       score <strong>8 / 10</strong>) with the current date and time, a sample certificate ID,
       and an auditable verification code — so you can preview the classical design locally.</p>
    <button type="button" id="previewBtn">Generate Sample Certificate</button>
    <div class="meta" id="meta"></div>
    <p class="note">This is a design preview only. Real certificates are issued from the
       weekly quiz after you submit your answers.</p>
  </main>
<script>
%%CERT_JS%%

  document.getElementById("previewBtn").addEventListener("click", function(){
    var info = generateCertificate({
      name: "Dr. Sample, MD, FRCPC",
      score: 8,
      total: 10,
      completedAt: new Date(),
      weekOf: "June 02, 2025"
    });
    if (info){
      document.getElementById("meta").textContent =
        "Certificate ID " + info.certId + "  ·  Verification " + info.verCode;
    }
  });
</script>
</body>
</html>"""
