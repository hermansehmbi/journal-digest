"""
Anesthesia Journal Digest — GitHub Pages Publisher
===================================================
Saves each episode's MP3 to docs/audio/ with a dated filename, regenerates a
styled HTML player at docs/index.html that plays the latest episode, then
commits and pushes so it is served from GitHub Pages.

GitHub Pages URL (project site, served from /docs on main):
    https://<your-username>.github.io/<repo-name>/
The exact URL is read from the GITHUB_PAGES_URL environment variable, which the
GitHub Actions workflow derives automatically from the repo context.
"""

from __future__ import annotations

import re
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def publish_episode(audio_file: str, date_obj: datetime | None = None,
                    push: bool = True) -> str | None:
    """Copy the MP3 into docs/audio/, rebuild the player page, commit + push.

    Returns the public GitHub Pages URL of the player page (the listener lands
    on the page and the latest episode auto-loads), or None on failure.
    """
    from config import GITHUB_PAGES_URL, DOCS_DIR, AUDIO_SUBDIR

    src = Path(audio_file)
    if not src.exists():
        logger.error(f"Audio file not found for publishing: {audio_file}")
        return None

    date_obj = date_obj or datetime.now()
    date_str = date_obj.strftime("%Y-%m-%d")

    docs = Path(DOCS_DIR)
    audio_dir = docs / AUDIO_SUBDIR
    audio_dir.mkdir(parents=True, exist_ok=True)

    dated_name = f"anesthesia-digest-{date_str}.mp3"
    dest = audio_dir / dated_name
    shutil.copyfile(src, dest)
    logger.info(f"Episode copied to {dest}")

    # Tell GitHub Pages not to run Jekyll (so audio/ is served verbatim).
    (docs / ".nojekyll").write_text("", encoding="utf-8")

    pretty_date = date_obj.strftime("%B %d, %Y")
    episodes = _list_episodes(audio_dir)
    html = _player_html(f"{AUDIO_SUBDIR}/{dated_name}", pretty_date, episodes)
    (docs / "index.html").write_text(html, encoding="utf-8")
    logger.info("Rebuilt docs/index.html player page")

    base = GITHUB_PAGES_URL.rstrip("/")
    player_url = f"{base}/"
    episode_url = f"{base}/{AUDIO_SUBDIR}/{dated_name}"

    if push:
        _git_publish(f"Publish podcast episode {date_str}")

    logger.info(f"Player URL: {player_url}")
    logger.info(f"Direct episode URL: {episode_url}")
    return player_url


def publish_cme_quiz(questions: list[dict], date_obj: datetime | None = None,
                     push: bool = True) -> str | None:
    """Write the week's interactive quiz to docs/cme/<date>.html, refresh the
    docs/cme/index.html listing, commit + push, and return the quiz's public
    GitHub Pages URL.
    """
    from config import GITHUB_PAGES_URL, DOCS_DIR
    from cme_quiz import build_quiz_page, build_quiz_index, build_cert_preview

    if not questions:
        logger.warning("No CME questions — skipping quiz publishing")
        return None

    date_obj = date_obj or datetime.now()
    date_str = date_obj.strftime("%Y-%m-%d")

    docs = Path(DOCS_DIR)
    cme_dir = docs / "cme"
    cme_dir.mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").write_text("", encoding="utf-8")

    page_name = f"{date_str}.html"
    (cme_dir / page_name).write_text(
        build_quiz_page(questions, date_obj), encoding="utf-8")
    logger.info(f"Wrote quiz page docs/cme/{page_name}")

    # Refresh the index from whatever dated quizzes exist.
    quizzes = _list_quizzes(cme_dir)
    (cme_dir / "index.html").write_text(
        build_quiz_index(quizzes), encoding="utf-8")
    logger.info("Rebuilt docs/cme/index.html")

    # Keep the certificate-design preview in sync with the current design.
    (cme_dir / "cert-preview.html").write_text(
        build_cert_preview(), encoding="utf-8")

    base = GITHUB_PAGES_URL.rstrip("/")
    quiz_url = f"{base}/cme/{page_name}"

    if push:
        _git_publish(f"Publish CME quiz {date_str}")

    logger.info(f"Quiz URL: {quiz_url}")
    return quiz_url


def publish_deepdive(summaries: list, date_obj: datetime | None = None,
                     push: bool = True) -> str | None:
    """Write the week's Deep Dive summaries to docs/deep-dive/<date>.html, refresh
    the docs/deep-dive/index.html listing, commit + push, and return the page's
    public GitHub Pages URL.
    """
    from config import GITHUB_PAGES_URL, DOCS_DIR
    from deepdive_builder import build_deepdive_page, build_deepdive_index

    if not summaries:
        logger.warning("No Deep Dive summaries — skipping page publishing")
        return None

    date_obj = date_obj or datetime.now()
    date_str = date_obj.strftime("%Y-%m-%d")

    docs = Path(DOCS_DIR)
    dd_dir = docs / "deep-dive"
    dd_dir.mkdir(parents=True, exist_ok=True)
    (docs / ".nojekyll").write_text("", encoding="utf-8")

    page_name = f"{date_str}.html"
    (dd_dir / page_name).write_text(
        build_deepdive_page(summaries, date_obj), encoding="utf-8")
    logger.info(f"Wrote Deep Dive page docs/deep-dive/{page_name}")

    pages = _list_deepdive(dd_dir)
    (dd_dir / "index.html").write_text(
        build_deepdive_index(pages), encoding="utf-8")
    logger.info("Rebuilt docs/deep-dive/index.html")

    base = GITHUB_PAGES_URL.rstrip("/")
    page_url = f"{base}/deep-dive/{page_name}"

    if push:
        _git_publish(f"Publish Deep Dive summaries {date_str}")

    logger.info(f"Deep Dive URL: {page_url}")
    return page_url


_DATED_QUIZ_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")


def _list_deepdive(dd_dir: Path) -> list[tuple[str, str]]:
    """Return [(YYYY-MM-DD, filename)] for dated Deep Dive pages, newest first."""
    out = []
    for page in dd_dir.glob("*.html"):
        if _DATED_QUIZ_RE.match(page.name):
            out.append((page.stem, page.name))
    out.sort(reverse=True)
    return out


def _list_quizzes(cme_dir: Path) -> list[tuple[str, str]]:
    """Return [(YYYY-MM-DD, filename)] for dated quiz pages, newest first.

    Only ``YYYY-MM-DD.html`` files count — helper pages like index.html,
    cert-preview.html, or sample.html are ignored.
    """
    out = []
    for page in cme_dir.glob("*.html"):
        if _DATED_QUIZ_RE.match(page.name):
            out.append((page.stem, page.name))
    out.sort(reverse=True)
    return out


def _list_episodes(audio_dir: Path) -> list[tuple[str, str]]:
    """Return [(YYYY-MM-DD, relative_path)] newest first for the archive list."""
    eps = []
    for mp3 in audio_dir.glob("anesthesia-digest-*.mp3"):
        date_part = mp3.stem.replace("anesthesia-digest-", "")
        eps.append((date_part, f"audio/{mp3.name}"))
    eps.sort(reverse=True)
    return eps


def _git_publish(message: str):
    """git add/commit/push the docs folder. Best-effort; logs on failure."""
    try:
        # Identity may be unset in CI; set a sensible default if so.
        if subprocess.run(["git", "config", "user.email"],
                          capture_output=True, text=True).returncode != 0 \
                or not subprocess.run(["git", "config", "user.email"],
                                      capture_output=True, text=True).stdout.strip():
            subprocess.run(["git", "config", "user.email",
                            "anesthesia-digest@users.noreply.github.com"], check=False)
            subprocess.run(["git", "config", "user.name",
                            "Anesthesia Digest Bot"], check=False)

        subprocess.run(["git", "add", "docs"], check=True)
        status = subprocess.run(["git", "status", "--porcelain", "docs"],
                                capture_output=True, text=True)
        if not status.stdout.strip():
            logger.info("No docs changes to publish")
            return
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push"], check=True)
        logger.info(f"Pushed to GitHub Pages: {message}")
    except subprocess.CalledProcessError as e:
        logger.error(f"git publish failed: {e}")
    except Exception as e:
        logger.error(f"git publish error: {e}")


def _player_html(latest_rel: str, pretty_date: str,
                 episodes: list[tuple[str, str]]) -> str:
    """Build a small, styled audio-player page for the latest episode."""
    archive_items = ""
    for date_part, rel in episodes:
        try:
            label = datetime.strptime(date_part, "%Y-%m-%d").strftime("%B %d, %Y")
        except ValueError:
            label = date_part
        archive_items += (
            f'<li><a href="{rel}">{label}</a></li>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anesthesia Digest — Audio Summary</title>
<style>
  :root {{ --navy:#1a5276; --blue:#2e86c1; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:linear-gradient(160deg,#0e2a3b,#1a5276); color:#eaf2f8;
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }}
  .card {{ background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.12);
          border-radius:18px; padding:32px; max-width:560px; width:100%;
          box-shadow:0 12px 40px rgba(0,0,0,0.35); backdrop-filter:blur(6px); }}
  .cover {{ width:120px; height:120px; border-radius:16px; margin:0 auto 20px;
           background:linear-gradient(135deg,var(--navy),var(--blue));
           display:flex; align-items:center; justify-content:center; font-size:54px; }}
  h1 {{ font-size:22px; text-align:center; margin:0 0 4px; }}
  .date {{ text-align:center; color:#aecbe0; font-size:14px; margin-bottom:20px; }}
  audio {{ width:100%; margin:10px 0 6px; }}
  .desc {{ font-size:13px; color:#cfe3f2; text-align:center; line-height:1.5; }}
  .disclaimer {{ font-size:11px; color:#8fb3cd; font-style:italic; text-align:center;
                margin-top:16px; line-height:1.5; }}
  .archive {{ margin-top:26px; border-top:1px solid rgba(255,255,255,0.12); padding-top:16px; }}
  .archive h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.05em;
                color:#aecbe0; margin:0 0 8px; }}
  .archive ul {{ list-style:none; padding:0; margin:0; max-height:160px; overflow:auto; }}
  .archive li {{ padding:5px 0; font-size:13px; }}
  .archive a {{ color:#9ecbf0; text-decoration:none; }}
  .archive a:hover {{ text-decoration:underline; }}
</style>
</head>
<body>
  <main class="card">
    <div class="cover">&#127911;</div>
    <h1>Anesthesia Digest — Audio Summary</h1>
    <div class="date">{pretty_date}</div>
    <audio controls preload="metadata" src="{latest_rel}"></audio>
    <p class="desc">A ~15&nbsp;minute AI-generated two-host discussion of today's journal highlights.</p>
    <p class="disclaimer">This audio was generated by AI (Claude&nbsp;+&nbsp;Edge&nbsp;TTS) and is not affiliated with any journal.</p>
    <section class="archive">
      <h2>Past episodes</h2>
      <ul>
{archive_items}      </ul>
    </section>
  </main>
</body>
</html>"""
