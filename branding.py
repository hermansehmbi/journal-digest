"""
Journal Digest — Branding pass
==============================
The engine keeps ONE set of HTML templates (emails + published pages), written
in the canonical anesthesia colours and brand strings. ``brandize()`` rewrites
the FINISHED HTML, swapping those canonical values for the active specialty's
(from config). For anesthesia the maps are identities, so its output is
byte-for-byte unchanged; EP and future specialties get a distinct accent colour
and brand text with zero extra template code.

Apply it once to the final HTML of every email and every published page.
"""

from __future__ import annotations

import config


def brandize(html: str) -> str:
    """Swap canonical anesthesia brand colours + name phrases for the active
    specialty's. Identity (no-op) for anesthesia."""
    if not html:
        return html
    # Brand phrases first (longest-first ordering is set in config), then colours.
    for old, new in config.BRAND_STRING_MAP:
        if old != new:
            html = html.replace(old, new)
    for old_hex, new_hex in config.COLOR_MAP.items():
        if old_hex.lower() != new_hex.lower():
            html = html.replace(old_hex, new_hex)
            html = html.replace(old_hex.upper(), new_hex)
    return html
