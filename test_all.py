"""
Journal Digest — Pre-Cutover Smoke Test
=======================================
Runs preview mode for both specialties (no email sent) and confirms each
produces a valid HTML email, then prints a per-journal article summary.

Checks:
  1. preview      --specialty=anesthesia  → valid digest HTML
  2. preview      --specialty=ep           → valid digest HTML
  3. preview-sat  --specialty=ep           → CME questions generate (needs
     ANTHROPIC_API_KEY and a prior `preview --specialty=ep`; otherwise reported
     as skipped, not failed)
  4. Per-journal article counts for both specialties.

Usage:
    python test_all.py
"""

from __future__ import annotations

import os
import re
import sys
import glob
import time
import subprocess
from pathlib import Path

PY = sys.executable
ROOT = Path(__file__).parent


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Free mode keeps the digest previews fast and key-free; preview-sat overrides
    # to api so CME actually generates when a key is present.
    if env_extra:
        env.update(env_extra)
    return subprocess.run([PY, "main.py", *args], cwd=ROOT, env=env,
                          capture_output=True, text=True)


def _newest_preview(specialty: str, label: str) -> Path | None:
    files = glob.glob(str(ROOT / f"preview_{specialty}_{label}_*.html"))
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def _valid_html(p: Path | None) -> bool:
    if not p or not p.exists():
        return False
    txt = p.read_text(encoding="utf-8", errors="replace")
    return len(txt) > 800 and "<html" in txt.lower() and "</html>" in txt.lower()


def _per_journal(stdout_stderr: str) -> list[tuple[str, int]]:
    # Fetcher logs lines like:  "  → 100 articles from HeartRhythm"
    out = []
    for m in re.finditer(r"→\s+(\d+)\s+articles from (\S+)", stdout_stderr):
        out.append((m.group(2), int(m.group(1))))
    return out


def check_digest(specialty: str) -> bool:
    print(f"\n[{specialty}] preview (digest) ...", flush=True)
    t0 = time.time()
    cp = _run(["preview", f"--specialty={specialty}"], {"MODE": "free"})
    blob = cp.stdout + cp.stderr
    p = _newest_preview(specialty, "digest")
    ok = _valid_html(p)
    print(f"  → {'OK' if ok else 'FAIL'} ({time.time()-t0:.0f}s) "
          f"{p.name if p else '(no file)'}")
    pj = _per_journal(blob)
    if pj:
        print(f"  per-journal article counts ({len(pj)} journals):")
        for abbr, n in pj:
            flag = "  ⚠ ZERO" if n == 0 else ""
            print(f"     {abbr:<16}{n:>4}{flag}")
    sel = re.search(r"Selected (\d+) articles", blob)
    if sel:
        print(f"  selected for email: {sel.group(1)}")
    if not ok:
        print("  --- stderr tail ---")
        print("\n".join(blob.splitlines()[-12:]))
    return ok


def check_saturday_ep() -> bool | None:
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print("\n[ep] preview-sat (CME) ...", flush=True)
    if not has_key:
        print("  → SKIPPED (no ANTHROPIC_API_KEY — CME needs the API)")
        return None
    # Saturday reads Monday's featured set; ensure it exists first.
    if not (ROOT / "monday_featured_ep.json").exists():
        _run(["preview", "--specialty=ep"], {"MODE": "free"})
    cp = _run(["preview-sat", "--specialty=ep"], {"MODE": "api"})
    blob = cp.stdout + cp.stderr
    p = _newest_preview("ep", "saturday")
    gen = re.search(r"Generated (\d+) CME questions", blob)
    ok = _valid_html(p) and bool(gen)
    print(f"  → {'OK' if ok else 'FAIL'} "
          f"{'('+gen.group(1)+' questions) ' if gen else ''}"
          f"{p.name if p else '(no file)'}")
    if not ok:
        print("\n".join(blob.splitlines()[-12:]))
    return ok


if __name__ == "__main__":
    results = {}
    results["anesthesia digest"] = check_digest("anesthesia")
    results["ep digest"] = check_digest("ep")
    results["ep CME"] = check_saturday_ep()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    failed = 0
    for name, r in results.items():
        if r is None:
            print(f"  - {name:<22} SKIPPED")
        elif r:
            print(f"  ✓ {name:<22} PASS")
        else:
            print(f"  ✗ {name:<22} FAIL")
            failed += 1
    sys.exit(1 if failed else 0)
