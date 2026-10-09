# Behavioral Execution & Accountability System — Specification

## 1. Core Philosophy
Traditional productivity apps treat task execution as a simple checkbox.
This system is an unyielding flight recorder designed to kill procrastination, audit excuses, and demand proof.

### Axioms:
1. Measure what actually happens, not what you intended.
2. No self-reported completion without verifiable artifacts.
3. Call out rationalizations immediately. Enforce a 5-minute micro-start floor.

---

## 2. Core State Machine & Intervention Cycle
- On time: Starts immediately (Latency = 0m).
- Delay 1 (Snooze 1): Adds 10 minutes.
- Delay 2 (Snooze 2): Forces Excuse Auditor prompt.
  - Valid blocker (medical/emergency): Clean reschedule.
  - Rationalization ("tired", "not feeling it"): Enforces 5-minute micro-session floor.

---

## 3. Formulas
- Starting Latency: Delta = max(0, t_started - t_scheduled)
- Execution Ratio: E = (Sum T_focused / Sum T_planned) * 100

---

## 4. Verification Tracks
1. Coding: AST/code inspection + 1-question viva (time/space complexity or edge cases).
2. T&P Aptitude: Test scorecard audit (questions attempted, accuracy %, time spent).
3. Theory: Notes/summary analysis + 1-question conceptual defense.
