"""
app/verification.py
AI-powered viva question generation and answer grading using Gemini 2.5 Flash.
Falls back to a heuristic response if GEMINI_API_KEY is missing or the API call fails.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gemini client (optional – graceful degradation if not installed/keyed)
# ---------------------------------------------------------------------------
try:
    from google import genai
    from google.genai import types as genai_types

    _API_KEY = os.getenv("GEMINI_API_KEY", "")
    if _API_KEY:
        _client = genai.Client(api_key=_API_KEY)
    else:
        _client = None
        logger.warning("GEMINI_API_KEY not set – AI verification will use heuristic fallback.")
except ImportError:
    _client = None
    logger.warning("google-genai not installed – AI verification will use heuristic fallback.")

_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_VIVA_GEN_SYSTEM = """You are an uncompromising technical examiner for a behavioral focus engine.
Your role: given a student's proof of work (code snippet, notes, or aptitude scorecard), generate exactly ONE hard technical viva question.

Rules:
1. For code → ask about time/space complexity OR a subtle edge-case the code does NOT handle.
2. For theory notes → probe the deepest mechanistic "why" behind one concept.
3. For aptitude scorecard → pick the weakest topic by accuracy and ask a question testing true understanding (not formula recall).
4. If pasted_burst_detected is True, preface your question with:
   "⚠️ PASTE ALERT: A large code block was pasted during this session. Justify every line."
5. If Git & Workspace Diff Telemetry is provided, tailor your technical question directly to the modified files, functions, lines, and logic.
6. Return ONLY the question — no preamble, no bullet points.
"""

_VIVA_EVAL_SYSTEM = """You are a brutally honest technical evaluator.
Evaluate the student's viva answer against the question provided. 

Output format (strict):
VERDICT: PASS or FAIL
CRITIQUE: 2-3 sentences of direct technical critique. No fluff, no encouragement. Only substance.

Grading criteria:
- PASS: Demonstrates genuine conceptual understanding with correct technical depth.
- FAIL: Vague, incorrect, memorized without understanding, or evasive."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_viva_question(
    proof_text: str,
    category: str,
    pasted_burst_detected: bool = False,
    git_context: Optional[str] = None,
) -> str:
    """
    Generate a single tough viva question based on the submitted proof and git telemetry.
    Returns a question string.
    """
    if _client is None:
        return _fallback_viva_question(category, pasted_burst_detected)

    paste_flag = "YES" if pasted_burst_detected else "NO"
    git_section = f"\nGit & Workspace Diff Telemetry:\n{git_context}\n" if git_context else ""
    user_content = (
        f"Category: {category}\n"
        f"Pasted burst detected: {paste_flag}\n"
        f"{git_section}"
        f"\n--- PROOF START ---\n{proof_text}\n--- PROOF END ---\n\n"
        "Generate the viva question now."
    )

    try:
        response = _client.models.generate_content(
            model=_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=_VIVA_GEN_SYSTEM,
                temperature=0.7,
                max_output_tokens=512,
            ),
        )
        return response.text.strip()
    except Exception as exc:
        logger.error("Viva question generation failed: %s", exc)
        return _fallback_viva_question(category, pasted_burst_detected)


def evaluate_viva_answer(
    question: str,
    answer: str,
    proof_text: str,
    category: str,
) -> tuple[bool, str]:
    """
    Grade the student's viva answer.
    Returns (passed: bool, feedback: str).
    """
    if _client is None:
        return _fallback_viva_eval(answer)

    user_content = (
        f"Category: {category}\n\n"
        f"VIVA QUESTION: {question}\n\n"
        f"STUDENT ANSWER: {answer}\n\n"
        f"ORIGINAL PROOF:\n{proof_text}"
    )

    try:
        response = _client.models.generate_content(
            model=_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=_VIVA_EVAL_SYSTEM,
                temperature=0.3,
                max_output_tokens=300,
            ),
        )
        raw = response.text.strip()
        passed = raw.upper().startswith("VERDICT: PASS")
        return passed, raw
    except Exception as exc:
        logger.error("Viva evaluation failed: %s", exc)
        return _fallback_viva_eval(answer)


def audit_excuse_with_ai(stated_reason: str, category: str) -> tuple[bool, str]:
    """
    Use Gemini to audit whether an excuse is a valid blocker.
    Returns (is_valid_blocker: bool, ai_response: str).
    """
    if _client is None:
        from app.crud import _heuristic_excuse_audit
        return _heuristic_excuse_audit(stated_reason)

    system = (
        "You are a no-nonsense accountability coach. "
        "Decide if the student's excuse for delaying their study session is a VALID BLOCKER "
        "(genuine emergency/medical) or a RATIONALIZATION. "
        "Output format:\n"
        "VERDICT: VALID or RATIONALIZATION\n"
        "RESPONSE: One sharp, direct sentence explaining your verdict and next action."
    )
    user_content = f"Task category: {category}\nExcuse: {stated_reason}"

    try:
        response = _client.models.generate_content(
            model=_MODEL,
            contents=user_content,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.2,
                max_output_tokens=150,
            ),
        )
        raw = response.text.strip()
        is_valid = "VERDICT: VALID" in raw.upper()
        return is_valid, raw
    except Exception as exc:
        logger.error("Excuse audit AI call failed: %s", exc)
        from app.crud import _heuristic_excuse_audit
        return _heuristic_excuse_audit(stated_reason)


# ---------------------------------------------------------------------------
# Fallbacks
# ---------------------------------------------------------------------------

def _fallback_viva_question(category: str, pasted_burst: bool) -> str:
    paste_prefix = "⚠️ PASTE ALERT: Justify every line you pasted. " if pasted_burst else ""
    fallbacks = {
        "coding": (
            f"{paste_prefix}What is the worst-case time complexity of your solution, "
            "and can you identify a specific input that triggers it?"
        ),
        "tnp": (
            f"{paste_prefix}In the topic you just studied, explain the single concept "
            "most students get wrong and why."
        ),
        "theory": (
            f"{paste_prefix}Explain from first principles why the core mechanism you studied "
            "works the way it does — without using any definitions."
        ),
    }
    return fallbacks.get(category, f"{paste_prefix}Explain the key concept you just worked on in depth.")


def _fallback_viva_eval(answer: str) -> tuple[bool, str]:
    if len(answer.strip()) < 30:
        return False, (
            "VERDICT: FAIL\nCRITIQUE: Answer is too brief to demonstrate any real understanding. "
            "Elaborate on the mechanics, not just the conclusion."
        )
    return True, (
        "VERDICT: PASS\nCRITIQUE: "
        "(AI unavailable – heuristic pass granted for answer length. "
        "Manual review recommended.)"
    )
