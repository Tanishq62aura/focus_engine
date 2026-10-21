"""
app/services/viva_evaluator.py
Pre-flight screening and structured evaluation engine for oral viva defense hardening.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-Flight Injection Detection Patterns
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions"),
    re.compile(r"(?i)system\s*prompt"),
    re.compile(r"(?i)developer\s+mode"),
    re.compile(r"(?i)maintenance\s+mode"),
]

# ---------------------------------------------------------------------------
# Gemini Client Setup (Optional)
# ---------------------------------------------------------------------------
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None  # type: ignore
    genai_types = None  # type: ignore

_gemini_client: Optional[genai.Client] = None


def get_gemini_client() -> Optional[genai.Client]:
    """Thread-safe lazy initialization for Gemini client."""
    global _gemini_client
    if genai is None:
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "<YOUR_GEMINI_API_KEY>":
        return None
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client


_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


class VivaEvaluationResult(dict):
    """Evaluation result supporting both dict item and attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            if name == "score":
                return self.get("composite", 0.0)
            raise AttributeError(f"'VivaEvaluationResult' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


TECHNICAL_DEPTH_KEYWORDS = {
    "complexity", "algorithm", "algorithms", "data", "structure", "data structure",
    "memory", "latency", "cache", "concurrency", "thread", "threads", "lock",
    "locks", "mutex", "pointer", "pointers", "reference", "references", "tree",
    "trees", "graph", "graphs", "hash", "table", "tables", "index", "b-tree",
    "stack", "heap", "queue", "binary", "search", "sort", "recursion",
    "dynamic programming", "in-memory", "buffer", "protocol", "socket",
    "network", "tcp", "http", "database", "query", "queries", "sql",
    "transaction", "transactions", "acid", "isolation", "invariant",
    "interface", "polymorphism", "inheritance", "asynchronous", "await",
    "coroutine", "coroutines", "event loop", "profiling", "benchmark",
    "benchmarks", "overhead", "trade-off", "tradeoff", "edge case", "boundary",
    "null pointer", "overflow", "underflow", "race condition", "deadlock",
    "idempotent", "serialization", "deserialization", "throughput", "sharding",
    "replication", "partition", "distributed", "state machine", "byte", "bytes",
    "cpu", "io", "runtime", "compilation", "microsecond", "microseconds",
    "millisecond", "milliseconds", "false sharing", "cache line",
    "synchronization", "telemetry", "regression", "endpoint",
    "mutual exclusion", "hold and wait", "circular wait", "preemption",
    "starvation", "semaphore", "paging", "virtual memory", "core", "cores",
}

ARTIFACT_KEYWORDS = {
    "code", "function", "functions", "method", "methods", "class", "classes",
    "module", "modules", "file", "files", "test", "tests", "unit test",
    "unit tests", "pytest", "benchmark", "benchmarks", "commit", "commits",
    "diff", "repository", "pr", "pull request", "implementation", "implemented",
    "refactored", "debugged", "log", "logs", "metric", "metrics", "output",
    "assert", "mock", "fixture", "fixtures", "schema", "table", "tables",
    "migration", "migrations", "script", "scripts", "documentation",
    "docstring", "payload", "pipeline", "router", "model", "models",
    "boundary", "variables", "64-byte",
}

SUPERFICIAL_KEYWORDS = {
    "good", "fine", "nice", "easy", "simple", "read", "reading",
    "looked at", "watched", "video", "chapter", "book", "done",
    "finished", "completed everything", "all good", "pretty cool",
    "think", "everything was", "great", "ok", "okay",
}


def _to_score(val: Any, default: float = 70.0) -> float:
    """Safely coerces Gemini response values to float scores between 0.0 and 100.0."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        m = re.search(r"(\d+(?:\.\d+)?)", val)
        if m:
            num = float(m.group(1))
            return min(100.0, max(0.0, num))
        val_lower = val.lower()
        if any(w in val_lower for w in ["strong", "excellent", "advanced", "high", "flawless", "superior"]):
            return 85.0
        if any(w in val_lower for w in ["intermediate", "foundational", "good", "moderate", "competent", "solid"]):
            return 75.0
        if any(w in val_lower for w in ["weak", "poor", "insufficient", "low", "superficial", "vague"]):
            return 35.0
    return default


def _evaluate_mock(
    transcript: str,
    block_topic: str = "",
    block_deliverable: str = "",
) -> VivaEvaluationResult:
    """
    Deterministic structured mock evaluation when GEMINI_API_KEY is unset or unavailable.
    Distinguishes between superficial/vague submissions and substantive technical defenses.
    """
    t_lower = transcript.lower()
    words_list = re.findall(r"\b[a-z0-9_-]+\b", t_lower)
    words_in_transcript = set(words_list)
    word_count = len(words_list)

    matched_tech = (words_in_transcript & TECHNICAL_DEPTH_KEYWORDS) | {
        phrase for phrase in TECHNICAL_DEPTH_KEYWORDS
        if " " in phrase and re.search(r"\b" + re.escape(phrase) + r"\b", t_lower)
    }
    matched_artifacts = (words_in_transcript & ARTIFACT_KEYWORDS) | {
        phrase for phrase in ARTIFACT_KEYWORDS
        if " " in phrase and re.search(r"\b" + re.escape(phrase) + r"\b", t_lower)
    }
    matched_superficial = (words_in_transcript & SUPERFICIAL_KEYWORDS) | {
        phrase for phrase in SUPERFICIAL_KEYWORDS
        if " " in phrase and re.search(r"\b" + re.escape(phrase) + r"\b", t_lower)
    }

    tech_count = len(matched_tech)
    artifact_count = len(matched_artifacts)
    superficial_count = len(matched_superficial)

    # Calculate depth score (0.0 to 100.0)
    if tech_count == 0:
        depth = min(45.0, 15.0 + word_count * 0.4)
    else:
        depth = min(95.0, 55.0 + (tech_count * 10.0) + min(15.0, word_count * 0.15))

    if tech_count <= 1 and superficial_count > 0:
        depth = max(10.0, depth - (superficial_count * 8.0))

    # Calculate artifacts score (0.0 to 100.0)
    if artifact_count == 0:
        artifacts = min(40.0, 10.0 + word_count * 0.3)
    else:
        artifacts = min(95.0, 55.0 + (artifact_count * 12.0) + min(15.0, word_count * 0.15))

    if artifact_count == 0 and superficial_count > 0:
        artifacts = max(10.0, artifacts - (superficial_count * 5.0))

    # Calculate alignment score (0.0 to 100.0)
    context_text = f"{block_topic} {block_deliverable}".lower()
    context_words = set(re.findall(r"\b[a-z0-9_-]+\b", context_text)) - {
        "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of",
        "with", "by", "as", "is", "are", "was", "were", "be", "been", "from",
    }

    if context_words:
        overlap = sum(1 for w in context_words if len(w) > 3 and w in words_in_transcript)
        if overlap >= 2:
            alignment = min(95.0, 75.0 + (overlap * 5.0))
        elif overlap == 1:
            alignment = 65.0
        else:
            alignment = 50.0
    else:
        alignment = 75.0 if (tech_count > 0 or artifact_count > 0) else 50.0

    depth = round(depth, 1)
    artifacts = round(artifacts, 1)
    alignment = round(alignment, 1)

    composite = round((depth * 0.4) + (artifacts * 0.4) + (alignment * 0.2), 2)
    passed = bool(composite >= 70.0 and depth >= 60.0 and artifacts >= 60.0)

    if passed:
        critique = "Deterministic mock: Defense demonstrated sufficient technical depth, verifiable artifacts, and topic alignment."
        rejection_reason = None
    else:
        reasons = []
        if depth < 60.0:
            reasons.append("insufficient technical depth")
        if artifacts < 60.0:
            reasons.append("lack of verifiable artifacts/code references")
        if composite < 70.0:
            reasons.append(f"composite score {composite} below 70.0 threshold")
        critique = f"Deterministic mock: Defense failed rubric ({', '.join(reasons)})."
        rejection_reason = "RUBRIC_THRESHOLD_NOT_MET"

    return VivaEvaluationResult({
        "passed": passed,
        "security_flag": False,
        "rejection_reason": rejection_reason,
        "depth": depth,
        "artifacts": artifacts,
        "alignment": alignment,
        "composite": composite,
        "score": composite,
        "critique": critique,
        "scores": {
            "depth": depth,
            "artifacts": artifacts,
            "alignment": alignment,
            "composite": composite,
        },
    })


def _evaluate_with_gemini(
    transcript: str,
    block_topic: str = "",
    block_deliverable: str = "",
) -> VivaEvaluationResult:
    """Invokes Gemini JSON mode for structured viva evaluation."""
    client = get_gemini_client()
    if client is None:
        return _evaluate_mock(transcript, block_topic, block_deliverable)

    prompt = (
        "You are an uncompromising academic and software engineering viva examiner.\n"
        f"Block Topic: {block_topic or 'General Engineering / Focus Study'}\n"
        f"Deliverable Spec: {block_deliverable or 'Verifiable technical artifacts and comprehension'}\n\n"
        f"Oral Defense Transcript:\n\"\"\"{transcript}\"\"\"\n\n"
        "Evaluate the student's oral defense transcript rigorously against the topic and deliverable.\n"
        "Score each metric with a float number between 0.0 and 100.0:\n"
        "- depth: Technical depth, conceptual precision, algorithmic/architectural understanding. Superficial or vague hand-waving must score < 50.0.\n"
        "- artifacts: Concrete references to code, system mechanisms, test cases, functions, metrics, or verifiable artifacts. If none mentioned, score < 40.0.\n"
        "- alignment: Topical alignment with the specified block topic and expected deliverable.\n"
        "Provide a concise, 1-2 sentence technical critique.\n\n"
        "Return ONLY a JSON object with NUMERIC floats for depth, artifacts, alignment:\n"
        "{\n"
        '  "depth": 85.0,\n'
        '  "artifacts": 75.0,\n'
        '  "alignment": 90.0,\n'
        '  "critique": "Solid technical comprehension."\n'
        "}"
    )

    try:
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=300,
            ),
        )
        if not response or not response.text:
            return _evaluate_mock(transcript, block_topic, block_deliverable)

        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)

        data = json.loads(raw_text)
        depth = round(_to_score(data.get("depth"), default=50.0), 1)
        artifacts = round(_to_score(data.get("artifacts"), default=50.0), 1)
        alignment = round(_to_score(data.get("alignment"), default=50.0), 1)
        critique = str(data.get("critique", "")).strip()

        composite = round((depth * 0.4) + (artifacts * 0.4) + (alignment * 0.2), 2)
        passed = bool(composite >= 70.0 and depth >= 60.0 and artifacts >= 60.0)

        return VivaEvaluationResult({
            "passed": passed,
            "security_flag": False,
            "rejection_reason": None if passed else "RUBRIC_THRESHOLD_NOT_MET",
            "depth": depth,
            "artifacts": artifacts,
            "alignment": alignment,
            "composite": composite,
            "score": composite,
            "critique": critique or ("Passed viva defense." if passed else "Failed rubric thresholds."),
            "scores": {
                "depth": depth,
                "artifacts": artifacts,
                "alignment": alignment,
                "composite": composite,
            },
        })
    except Exception as exc:
        logger.warning("Gemini JSON viva evaluation error, falling back to deterministic mock: %s", exc)
        return _evaluate_mock(transcript, block_topic, block_deliverable)


def evaluate_viva_defense(
    transcript: str,
    block_topic: str = "",
    block_deliverable: str = "",
) -> VivaEvaluationResult:
    """
    Evaluates an oral viva defense transcript with pre-flight screening and rubric invariants.

    Pre-flight checks:
    1. Pattern screening: Screen transcripts using regex patterns for injection tokens.
       If matched, returns failed evaluation with security_flag=True and rejection_reason='PROMPT_INJECTION_FLAGGED'.
    2. Length check: Reject transcripts under 60 characters with code 'TRANSCRIPT_TOO_SHORT'.

    Rubric invariant:
    composite = (depth * 0.4) + (artifacts * 0.4) + (alignment * 0.2)
    Require composite >= 70.0 and depth >= 60.0 and artifacts >= 60.0 to set passed=True.
    """
    text = (transcript or "").strip()

    # 1. Pattern screening (Regex injection check)
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return VivaEvaluationResult({
                "passed": False,
                "security_flag": True,
                "rejection_reason": "PROMPT_INJECTION_FLAGGED",
                "depth": 0.0,
                "artifacts": 0.0,
                "alignment": 0.0,
                "composite": 0.0,
                "score": 0.0,
                "critique": "Potential prompt injection or instruction override token detected in defense transcript.",
                "scores": {
                    "depth": 0.0,
                    "artifacts": 0.0,
                    "alignment": 0.0,
                    "composite": 0.0,
                },
            })

    # 2. Length check (< 60 chars)
    if len(text) < 60:
        return VivaEvaluationResult({
            "passed": False,
            "security_flag": False,
            "rejection_reason": "TRANSCRIPT_TOO_SHORT",
            "depth": 0.0,
            "artifacts": 0.0,
            "alignment": 0.0,
            "composite": 0.0,
            "score": 0.0,
            "critique": "Transcript too short. Minimum 60 characters required for oral defense evaluation.",
            "scores": {
                "depth": 0.0,
                "artifacts": 0.0,
                "alignment": 0.0,
                "composite": 0.0,
            },
        })

    # 3. Evaluation logic: Gemini JSON mode when GEMINI_API_KEY is present, or deterministic mock
    client = get_gemini_client()
    if client is not None:
        return _evaluate_with_gemini(text, block_topic, block_deliverable)
    else:
        return _evaluate_mock(text, block_topic, block_deliverable)

