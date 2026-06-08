"""
Anesthesia Journal Digest — CME Question Generator
====================================================
Uses Claude API to generate clinical self-assessment questions from the week's
articles. This is a self-assessment tool to support self-directed learning.
Time spent reading/reflecting is self-reportable under RCPSC MOC Section 2
(Self-Learning) at the physician's discretion.
"""

from __future__ import annotations

import os
import re
import json
import random
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_LETTERS = ["A", "B", "C", "D"]


def generate_cme_questions(articles: list, num_questions: int = 10,
                           summaries: list | None = None) -> list[dict] | None:
    """
    Generate CME-style MCQs from the week's articles.

    If ``summaries`` (the Monday Deep Dive summaries) are provided, each question
    is grounded in the SAME structured summary the reader saw — keeping the quiz
    and the Deep Dive consistent and skipping a redundant full-text re-fetch.
    Articles without a matching summary fall back to the cached full text.

    Returns list of question dicts:
        {
            "question": str,
            "options": {"A": str, "B": str, "C": str, "D": str},
            "correct": "A|B|C|D",   # the letter of the correct option (randomized)
            "rationale": str,
            "source_article": str,
            "source_journal": str,
            "source_url": str,
            "rcpsc_section": "Section 2 & 3",
        }
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from config import ANTHROPIC_API_KEY
        api_key = ANTHROPIC_API_KEY

    if not api_key:
        logger.error("No ANTHROPIC_API_KEY. Skipping CME generation.")
        return None

    if not articles:
        logger.warning("No articles for CME generation")
        return None

    # Haiku (cheap model) for CME generation — streaming keeps it from timing out.
    from config import CLAUDE_MODEL_FAST, CME_SOURCE_CHARS

    # One question per article. Keep every provided article (they're Monday's
    # featured set); just order by impact and cap at num_questions.
    candidates = sorted(articles, key=lambda a: a["impact_factor"],
                        reverse=True)[:num_questions]
    # Match the question count to the actual number of articles so the prompt's
    # "one question per article" instruction is exactly satisfiable.
    num_questions = len(candidates)
    if num_questions == 0:
        logger.warning("No articles to build CME questions from")
        return None

    # Fetch (and cache) full text ONLY for these CME articles so explanations
    # can quote real statistics. Open access → parsed Results/Discussion/etc.;
    # paywalled → abstract only.
    try:
        from fulltext_resolver import get_fulltext as get_article_text
    except Exception:
        get_article_text = None

    # Map article URL -> Monday's Deep Dive summary (Option B: reuse, don't refetch).
    summary_by_url = {s.get("url"): s for s in (summaries or []) if s.get("url")}

    briefs = []
    for i, art in enumerate(candidates, 1):
        s = summary_by_url.get(art.get("url"))
        if s:
            # Ground the question in the same structured summary the reader saw.
            body = _compose_summary_text(s)
            if s.get("source") == "fulltext":
                access = ("GROUNDED SUMMARY of the open-access full text (you may "
                          "quote a real number only if it appears below)")
            else:
                access = ("ABSTRACT-BASED SUMMARY — describe direction/magnitude of "
                          "findings; do NOT invent any statistic not present below")
            briefs.append(
                f"[{i}] Journal: {art['journal_abbr']} | Title: {art['title']}\n"
                f"URL: {art['url']}\n"
                f"Access: {access}\n"
                f"Source text:\n{body[:2400]}"
            )
            continue

        # Fallback: no summary for this article — fetch (cached) full text.
        info = {"source": "abstract", "text": art.get("abstract", "")}
        if get_article_text:
            try:
                info = get_article_text(art)
            except Exception as e:
                logger.warning(f"Full-text fetch failed for {art.get('url')}: {e}")

        body = (info.get("text") or art.get("abstract", "")).strip()
        if info.get("source") == "fulltext":
            access = "OPEN-ACCESS FULL TEXT (quote real numbers from the sections below)"
        elif body:
            access = ("ABSTRACT ONLY — paywalled. Describe direction/magnitude of "
                      "findings; do NOT invent any statistic not present below")
        else:
            access = "NO SOURCE TEXT AVAILABLE — keep the explanation qualitative"

        briefs.append(
            f"[{i}] Journal: {art['journal_abbr']} | Title: {art['title']}\n"
            f"URL: {art['url']}\n"
            f"Access: {access}\n"
            f"Source text:\n{body[:CME_SOURCE_CHARS]}"
        )
    articles_text = "\n\n".join(briefs)
    if summary_by_url:
        used = sum(1 for art in candidates if art.get("url") in summary_by_url)
        logger.info(f"CME grounding: {used}/{len(candidates)} from Monday's "
                    f"Deep Dive summaries, rest from full text")

    # A specialty may supply its own CME prompt template (with {articles_text}
    # and {num_questions} fields). When absent, use the built-in anesthesia
    # default below — keeping anesthesia output unchanged.
    from config import CME_PROMPT as _cfg_cme_prompt
    if _cfg_cme_prompt:
        prompt = _cfg_cme_prompt.format(articles_text=articles_text,
                                        num_questions=num_questions)
    else:
        prompt = f"""You are writing clinical self-assessment questions on recent anesthesia
literature for practising anesthesiologists. These support self-directed study (the kind a
physician may self-report under RCPSC Section 2 Self-Learning); do not state or imply that
completing them awards any formal credit.

TOPIC GUIDANCE (internal only — for choosing what to test): favor topics that matter at the
bedside for a generalist anesthesiologist (airway, analgesia, obstetric anesthesia, regional
techniques, perioperative management, patient safety).
HARD RULE: NEVER write the phrase "community anesthesiologist" (or "community anaesthetist",
or "community anesthetist") anywhere in a question, option, or explanation. That phrase is
internal guidance only and must not appear in the output.

ARTICLES FOR THIS WEEK (each has source text — full text if open access, otherwise abstract):
{articles_text}

Generate EXACTLY {num_questions} single-best-answer multiple-choice questions — ONE question
per article, using EACH article in the list exactly once (there are {num_questions} articles).
No two questions may come from the same article, and no two may test the same teaching point.

For each question:
1. Make it a REALISTIC CLINICAL question with specific patient details (age, sex,
   comorbidities, procedure, doses, monitoring, or context) — e.g. "A 72-year-old undergoes
   elective hip arthroplasty under spinal anesthesia. Which intervention best reduces
   postoperative delirium?" Do NOT describe the target audience in the question.
2. Focus on CLINICAL DECISION-MAKING, not statistics. Test things like: best management step,
   drug or dose choice, technique selection, recognizing/managing a complication, monitoring,
   or applying the article's finding to the patient. AT MOST 1-2 of the {num_questions}
   questions may be about interpreting statistical results (effect sizes, CIs, etc.) — the
   rest must be clinical-application questions, NOT statistics questions.
3. Provide exactly 4 options (A-D): ONE clearly correct answer and three plausible
   distractors. Avoid "all/none of the above".
4. Write a CONCISE explanation of 5 to 10 LINES MAXIMUM (do NOT exceed 10 lines). State why
   the correct choice is right with clinical reasoning, then briefly why the main distractors
   are wrong. You MAY mention one real effect size/number from the article's Source text if it
   adds value, but keep it brief — do not pad with statistics. Refer to choices BY THEIR
   CONTENT/WORDING, never by letter (the option order is randomized afterwards).
   Use ONLY numbers that actually appear in the Source text; if abstract-only or a number
   isn't present, stay qualitative. NEVER fabricate statistics, CIs, or p-values.
5. Reference the source article (use its exact title, journal, and URL from the list).
6. VARY which option is correct across the set (positions are also shuffled afterwards).

RESPOND IN THIS EXACT JSON FORMAT (no markdown, no backticks, just raw JSON):
[
  {{
    "question": "A 65-year-old ... <clinical scenario> ... What is the best ... ?",
    "options": {{
      "A": "<distinct option text>",
      "B": "<distinct option text>",
      "C": "<distinct option text>",
      "D": "<distinct option text>"
    }},
    "correct": "<the letter of the correct option>",
    "rationale": "<5-10 lines max. Clinical reasoning for the correct choice, brief note on why the main distractors are wrong, optionally ONE real figure from the Source text. Name choices by wording, not letter.>",
    "source_article": "Exact title of the source article",
    "source_journal": "<journal abbreviation>",
    "source_url": "https://..."
  }}
]

Generate exactly {num_questions} clinically-focused questions — one per article, each testing
a different point, explanations 5-10 lines."""

    try:
        # Stream the response so a long generation never trips a single read
        # timeout (the previous non-streaming call timed out at 120s).
        text = _stream_message(api_key, CLAUDE_MODEL_FAST, prompt, max_tokens=8192)
        if not text.strip():
            logger.error("CME stream returned no text")
            return None

        # Parse JSON (strip any accidental markdown fences)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        questions = json.loads(text)

        # Randomize the position of the correct option so there is no positional
        # bias (the model tends to favor one letter). This also remaps any stray
        # "Option X" references in the rationale to the new letters.
        _shuffle_options(questions)

        # Add RCPSC section tag
        for q in questions:
            q["rcpsc_section"] = "Section 2 & 3"

        dist = {L: sum(1 for q in questions if q.get("correct") == L) for L in _LETTERS}
        logger.info(f"Generated {len(questions)} CME questions; "
                    f"correct-answer distribution {dist}")
        return questions

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse CME JSON: {e}")
        logger.debug(f"Raw response: {text[:500]}")
        return None
    except Exception as e:
        logger.error(f"Claude API call failed for CME: {e}")
        return None


def _compose_summary_text(s: dict) -> str:
    """Flatten a Deep Dive summary dict into labelled source text for grounding."""
    order = [
        ("Bottom line", "bottom_line"),
        ("What they did", "what_they_did"),
        ("Design", "design"),
        ("Findings", "what_they_found"),
        ("Interpretation", "what_it_means"),
        ("Why it matters", "why_it_matters"),
        ("Limitations", "limitations"),
    ]
    return "\n".join(f"{label}: {s[key]}" for label, key in order if s.get(key))


# ── Streaming API call ───────────────────────────────────────────────────────

def _stream_message(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    """Call the Anthropic Messages API in streaming mode and return the full text.

    Streaming avoids a single long read timeout: the per-read timeout only has to
    cover the gap between SSE chunks (which arrive continuously), not the whole
    multi-minute generation. Same model and output as a non-streaming call.
    """
    import httpx

    # connect/read/write/pool — read is the max gap BETWEEN streamed chunks.
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
    parts: list[str] = []
    with httpx.stream(
        "POST",
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    ) as response:
        if response.status_code != 200:
            body = response.read().decode("utf-8", "replace")
            raise RuntimeError(f"Anthropic API HTTP {response.status_code}: {body[:300]}")

        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "content_block_delta":
                delta = evt.get("delta", {})
                if delta.get("type") == "text_delta":
                    parts.append(delta.get("text", ""))
            elif etype == "error":
                raise RuntimeError(f"Anthropic stream error: {evt.get('error')}")
            elif etype == "message_stop":
                break

    text = "".join(parts)
    logger.info(f"CME stream assembled {len(text)} chars ({len(text.split())} words)")
    return text


# ── Answer-position randomization ────────────────────────────────────────────

def _shuffle_options(questions: list, rng: random.Random | None = None) -> list:
    """Shuffle each question's A-D options in place and update the correct letter.

    Tracks the correct answer by its TEXT (not its letter), so after shuffling
    the ``correct`` field points at whichever letter now holds the right answer.
    Any explicit "Option X" letter references in the rationale are remapped to
    the new letters as a safety net (the prompt asks the model to avoid them).
    """
    rng = rng or random
    for q in questions:
        opts = q.get("options") or {}
        correct = q.get("correct")
        items = [(L, opts.get(L)) for L in _LETTERS if opts.get(L)]
        if len(items) < 2 or correct not in dict(items):
            continue

        correct_text = dict(items)[correct]
        texts = [t for _, t in items]
        rng.shuffle(texts)

        new_opts = {}
        new_correct = correct
        for i, t in enumerate(texts):
            L = _LETTERS[i]
            new_opts[L] = t
            if t == correct_text:
                new_correct = L

        # old letter -> new letter, matched by identical option text
        text_to_new = {t: _LETTERS[i] for i, t in enumerate(texts)}
        old_to_new = {oldL: text_to_new.get(t, oldL) for oldL, t in items}

        q["options"] = new_opts
        q["correct"] = new_correct
        q["rationale"] = _remap_letters(q.get("rationale", ""), old_to_new)
    return questions


# Match clear option-letter references like "Option B", "choice C", "answer is A".
_LETTER_REF_RE = re.compile(
    r"(?P<pre>\b[Oo]ptions?\s+|\b[Cc]hoices?\s+|\banswer(?:\s+is|:)?\s+)(?P<L>[A-D])\b")


def _remap_letters(text: str, mapping: dict) -> str:
    """Remap option-letter references in a rationale using old->new mapping.

    Uses a single pass so swaps never chain (e.g. A->C and C->A stay distinct).
    """
    if not text:
        return text

    def repl(m):
        return m.group("pre") + mapping.get(m.group("L"), m.group("L"))

    return _LETTER_REF_RE.sub(repl, text)
