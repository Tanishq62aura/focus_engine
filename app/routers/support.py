"""
app/routers/support.py
Embedded AI Support and Query Assistant using Gemini 2.5 Flash.
Acts as an operational coach, answering queries on app mechanics, study tracks, and initiation resistance.
"""
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import FocusSession
from app.schemas import SupportChatRequest, SupportChatResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Gemini Client Setup
# ---------------------------------------------------------------------------
try:
    from google import genai
    from google.genai import types as genai_types

    _API_KEY = os.getenv("GEMINI_API_KEY", "")
    if _API_KEY and _API_KEY != "<YOUR_GEMINI_API_KEY>":
        _client = genai.Client(api_key=_API_KEY)
    else:
        _client = None
except ImportError:
    _client = None

_MODEL = "gemini-2.5-flash"

_SYSTEM_PROMPT = """You are the Focus Engine Execution Coach and System Operator.
Your mission is to provide concise, tactical, high-leverage execution guidance for focused study and deep work.

System Architecture Knowledge:
1. Behavioral Accountability:
   - 3-Tier Goals: Minimum (activation floor to break inertia), Target (standard completion objective), Stretch (flow ceiling).
   - Viva Defense: After session completion, student submits proof (code, notes, scorecard, summary) and must pass a probing technical exam.
   - 5-Minute Micro-Start Gate: Enforced on high-friction tracks (>75 resistance) or critical domains. Imposes a 25m Pomodoro cap, physical phone boundary (>3m), and bypasses proof requirements during the initial window.
   - System Friction Index F in [0, 100]: Multi-factor resistance metric tracking initiation latency, interruptions, and snooze excuses.

2. Coaching Guidelines:
   - Provide direct, concise, high-impact guidance (<120 words).
   - When asked how to tackle novel/non-coding domains (writing, research, reading, design), break them into atomic, verifiable checkpoints.
   - When asked how viva defense judges non-code work, explain that it evaluates conceptual depth, logical coherence, and deductive reasoning rather than rote recall.
   - Tone: Stoic, disciplined, razor-sharp, and highly actionable. No unnecessary pleasantries.
"""


def _generate_fallback_response(query: str) -> str:
    q = query.lower()
    if "viva" in q or "proof" in q:
        return (
            "[Viva Defense Evaluation Protocol]\n"
            "- Code tracks: Evaluates time/space complexity, edge cases, and paste legitimacy.\n"
            "- Non-code tracks (theory, research, writing): Probes logical synthesis, core mechanisms, and causal deduction.\n"
            "Tip: Ensure your proof contains specific notes, outlines, or arguments so the examiner can formulate a precise question."
        )
    elif "micro" in q or "friction" in q or "start" in q or "resistance" in q:
        return (
            "[5-Minute Micro-Start Protocol]\n"
            "1. Phone Isolation: Relocate physical phone >3 meters away.\n"
            "2. Atomic Milestone: Define exactly 1 question, heading, or test case.\n"
            "3. Dirty Start: Write immediate rough draft without perfectionism.\n"
            "Once the 5-minute activation floor passes, initial inertia drops by ~80%."
        )
    elif "break down" in q or "split" in q or "goal" in q:
        return (
            "[Atomic Session Breakdown]\n"
            "- Min Goal (15-25m): Complete the basic framework or read key sections.\n"
            "- Target Goal (45-50m): Draft the primary deliverable or solve the main problem.\n"
            "- Stretch Goal (60-90m): Refine, verify edge cases, and write defense proof notes."
        )
    return (
        "[Focus Engine Operational Guidance]\n"
        "- Establish your 3-tier goal before starting.\n"
        "- Log every peripheral urge with 1-Tap Distraction buttons.\n"
        "- At session conclusion, summarize proof notes for your AI viva defense.\n"
        "Stay locked in. Action precedes motivation."
    )


@router.post("/chat", response_model=SupportChatResponse)
def support_chat(
    payload: SupportChatRequest,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    user_msg = payload.message.strip()
    session_ctx = ""
    if payload.session_id:
        sess = db.query(FocusSession).filter(FocusSession.id == payload.session_id).first()
        if sess and sess.task:
            session_ctx = f"\n[Active Context: Task '{sess.task.title}', Category '{sess.task.category}']\n"

    if _client is not None:
        try:
            full_prompt = f"{session_ctx}User Question: {user_msg}\n\nProvide tactical coaching response:"
            response = _client.models.generate_content(
                model=_MODEL,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    temperature=0.6,
                    max_output_tokens=300,
                ),
            )
            if response and response.text:
                return SupportChatResponse(
                    reply=response.text.strip(),
                    session_id=payload.session_id,
                )
        except Exception as err:
            logger.warning("Gemini support chat call failed: %s. Using heuristic fallback.", err)

    reply = _generate_fallback_response(user_msg)
    return SupportChatResponse(reply=reply, session_id=payload.session_id)
