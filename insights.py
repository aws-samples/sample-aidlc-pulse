"""Derived signals for the dashboard: what needs the person, stalls and errors, and where time went.

Pure functions over the stage list and parsed audit events that reader.py builds. Nothing here reads files.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

DEFAULT_STALL_MINUTES = 15
MIN_ETA_SAMPLES = 2
RECENT_ERROR_LIMIT = 50
QUESTIONS_SUFFIX = "-questions.md"
WORKING_STATUSES = ("in-progress", "revising")
DONE_STATUSES = ("completed", "skipped")

# Stage-event state machine for time accounting: event -> segment kind opened after it (None closes).
_OPENS = {
    "STAGE_STARTED": "agent",
    "STAGE_AWAITING_APPROVAL": "human",
    "GATE_REJECTED": "rework",
    "STAGE_REVISING": "rework",
    "GATE_APPROVED": None,
    "STAGE_COMPLETED": None,
    "STAGE_SKIPPED": None,
    # Per-unit Construction receipts, timed per (stage, unit).
    "UNIT_STARTED": "agent",
    "UNIT_RESUMED": "agent",
    "UNIT_PAUSED": None,
    "UNIT_COMPLETED": None,
    "UNIT_SKIPPED": None,
}
_RESTARTS = ("STAGE_STARTED", "UNIT_STARTED", "UNIT_RESUMED")
# Segments that only run while an agent is active; a person's wait (human) keeps running while the run is idle.
_AGENT_KINDS = ("agent", "rework")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _seconds(start: datetime | None, end: datetime | None) -> float:
    return max((end - start).total_seconds(), 0.0) if start and end else 0.0


# --------------------------------------------------------------------------- 1. needs you


def attention(stages: list[dict[str, Any]], events: list[dict[str, Any]], sessions: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Open gates (with their questions files) and engine questions waiting on the person."""
    gate_opened = _last_event_time(events, "STAGE_AWAITING_APPROVAL")
    gates = []
    for stage in stages:
        if stage["status"] != "awaiting-approval":
            continue
        since = gate_opened.get(stage["slug"])
        gates.append({
            "slug": stage["slug"],
            "id": stage.get("id"),
            "since": _iso(since),
            "waitSeconds": _seconds(since, now),
            "questionFiles": [a["path"] for a in stage.get("artifacts", []) if a["name"].endswith(QUESTIONS_SUFFIX)],
        })
    run_start = min((t for e in events if e["event"] == "WORKFLOW_STARTED" and (t := parse_ts(e["timestamp"]))), default=None)
    finished = all(s["status"] in DONE_STATUSES for s in stages if s["decision"] == "EXECUTE")
    questions = [] if finished else [q for q in sessions.get("questions", [])
                                     if (q.get("text") or "").strip() and _after(q.get("createdAt"), run_start)]
    return {"gates": gates, "questions": questions, "count": len(gates) + len(questions)}


def _after(value: str | None, start: datetime | None) -> bool:
    """Engine questions written before this run began belong to an earlier conversation."""
    ts = parse_ts(value)
    return start is None or (ts is not None and ts >= start)


def _last_event_time(events: list[dict[str, Any]], name: str) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    for ev in events:
        if ev["event"] == name and ev.get("stage") and (ts := parse_ts(ev["timestamp"])):
            found[ev["stage"]] = ts
    return found


# --------------------------------------------------------------------------- 2. stalls and errors


def health(stages: list[dict[str, Any]], events: list[dict[str, Any]], now: datetime, stall_minutes: int) -> dict[str, Any]:
    """A working stage with no audit activity for `stall_minutes` is flagged as possibly stalled."""
    last = max((t for e in events if (t := parse_ts(e["timestamp"]))), default=None)
    idle = _seconds(last, now)
    working = [s["slug"] for s in stages if s["status"] in WORKING_STATUSES]
    errors = [_error_row(e) for e in events if e["event"] == "ERROR_LOGGED"]
    active_stages = set(working) | {s["slug"] for s in stages if s["status"] == "awaiting-approval"}
    return {
        "lastEventAt": _iso(last),
        "idleSeconds": idle,
        "stallMinutes": stall_minutes,
        "stalled": bool(working) and idle >= stall_minutes * 60,
        "workingStages": working,
        "errorCount": len(errors),
        "activeStageErrors": sum(1 for e in errors if e["stage"] in active_stages),
        "errors": errors[-RECENT_ERROR_LIMIT:][::-1],
    }


def _error_row(ev: dict[str, Any]) -> dict[str, Any]:
    fields = ev["fields"]
    return {"timestamp": ev["timestamp"], "stage": ev.get("stage"), "tool": fields.get("Tool"),
            "command": fields.get("Command"), "error": fields.get("Error"), "exitCode": fields.get("Exit Code")}


# --------------------------------------------------------------------------- 4. where time went


def timing(stages: list[dict[str, Any]], events: list[dict[str, Any]], now: datetime,
           stall_minutes: int = DEFAULT_STALL_MINUTES) -> dict[str, Any]:
    """Per-stage segments split into agent work, waiting on the person, and rework; plus totals and an ETA.

    Stage events and per-unit (UNIT_*) receipts both count. Once the run has been idle for `stall_minutes`,
    open agent/rework segments stop at the last audit event instead of running on with the wall clock.
    """
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        if ev["event"] in _OPENS and ev.get("stage") and not ev.get("stageInferred"):
            by_stage.setdefault(ev["stage"], []).append(ev)
    idle_since = _idle_since(events, now, stall_minutes)
    status = {s["slug"]: s["status"] for s in stages}
    rows = []
    for stage in stages:
        evs = by_stage.get(stage["slug"]) or by_stage.get(stage.get("stateSlug", ""))
        if evs:
            rows.append(_stage_timing(stage, evs, status[stage["slug"]] not in DONE_STATUSES, now, idle_since))
    return {"stages": rows, "totals": _totals(rows), "eta": _eta(stages, rows, now), "now": _iso(now),
            "idleSince": _iso(idle_since)}


def _idle_since(events: list[dict[str, Any]], now: datetime, stall_minutes: int) -> datetime | None:
    """Time of the last audit event when the run has gone quiet for `stall_minutes` or more; else None."""
    last = max((t for e in events if (t := parse_ts(e["timestamp"]))), default=None)
    return last if last and _seconds(last, now) >= stall_minutes * 60 else None


def _segment(kind: str, start: datetime, end: datetime, unit: str | None, **extra: Any) -> dict[str, Any]:
    seg = {"kind": kind, "start": _iso(start), "end": _iso(end), "seconds": _seconds(start, end), **extra}
    if unit:
        seg["unit"] = unit
    return seg


def _stage_timing(stage: dict[str, Any], evs: list[dict[str, Any]], still_open: bool, now: datetime,
                  idle_since: datetime | None) -> dict[str, Any]:
    segments, open_ = _closed_segments(evs)
    if still_open:
        for unit, (kind, start) in open_.items():
            if idle_since and kind in _AGENT_KINDS and idle_since >= start:
                segments.append(_segment(kind, start, idle_since, unit, idle=True))
            else:
                segments.append(_segment(kind, start, now, unit, open=True))
    segments.sort(key=lambda s: s["start"])
    sums = {k: sum(s["seconds"] for s in segments if s["kind"] == k) for k in ("agent", "human", "rework")}
    return {
        "slug": stage["slug"], "id": stage.get("id"), "phase": stage.get("phase"), "status": stage["status"],
        "lead": (stage.get("meta") or {}).get("lead"),
        "segments": segments, **sums, "total": sum(sums.values()),
        "rejections": sum(1 for e in evs if e["event"] == "GATE_REJECTED"),
    }


def _closed_segments(evs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str | None, tuple[str, datetime]]]:
    """Run each (stage, unit) stream's state machine. Returns (closed segments, {unit: (kind, start)} still open).

    Stage-level events (no Unit) form one stream; each Unit gets its own. When per-unit work begins, the
    stage-level agent segment closes, so the stage's entry is not counted on top of its units' time.
    """
    segments: list[dict[str, Any]] = []
    open_: dict[str | None, tuple[str, datetime]] = {}
    for ev in evs:
        ts = parse_ts(ev["timestamp"])
        if ts is None:
            continue
        unit = (ev.get("fields") or {}).get("Unit") or None
        if unit and None in open_ and open_[None][0] in _AGENT_KINDS:
            kind, start = open_.pop(None)
            segments.append(_segment(kind, start, ts, None))
        prev = open_.pop(unit, None)
        if prev:
            segments.append(_segment(prev[0], prev[1], ts, unit))
        nxt = prev[0] if prev and ev["event"] in _RESTARTS else _OPENS[ev["event"]]  # a restart keeps the current kind
        if nxt:
            open_[unit] = (nxt, ts)
    return segments, open_
    sums = {k: sum(s["seconds"] for s in segments if s["kind"] == k) for k in ("agent", "human", "rework")}
    return {
        "slug": stage["slug"], "id": stage.get("id"), "phase": stage.get("phase"), "status": stage["status"],
        "lead": (stage.get("meta") or {}).get("lead"),
        "segments": segments, **sums, "total": sum(sums.values()),
        "rejections": sum(1 for e in evs if e["event"] == "GATE_REJECTED"),
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {k: sum(r[k] for r in rows) for k in ("agent", "human", "rework", "total")}
    longest = max(rows, key=lambda r: r["total"], default=None)
    by_phase: dict[str, dict[str, float]] = {}
    for r in rows:
        phase = by_phase.setdefault(r["phase"] or "other", {"agent": 0.0, "human": 0.0, "rework": 0.0})
        for k in phase:
            phase[k] += r[k]
    return {**totals, "rejections": sum(r["rejections"] for r in rows),
            "longest": {"slug": longest["slug"], "seconds": longest["total"]} if longest else None, "byPhase": by_phase}


def _eta(stages: list[dict[str, Any]], rows: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Rough finish estimate: median completed-stage duration x remaining in-scope stages."""
    done = [r["total"] for r in rows if r["status"] == "completed" and r["total"] > 0]
    remaining = [s for s in stages if s["decision"] == "EXECUTE" and s["status"] not in DONE_STATUSES]
    if not remaining:
        return {"remainingStages": 0, "seconds": 0, "finishAt": None, "basis": "nothing left"}
    if len(done) < MIN_ETA_SAMPLES:
        return None
    median = statistics.median(done)
    elapsed = {r["slug"]: r["total"] for r in rows}
    seconds = sum(max(median - elapsed.get(s["slug"], 0.0), 0.0) for s in remaining)
    finish = datetime.fromtimestamp(now.timestamp() + seconds, tz=timezone.utc)
    return {"remainingStages": len(remaining), "seconds": seconds, "finishAt": _iso(finish),
            "basis": f"median of {len(done)} finished stages ({round(median / 60, 1)} min each)"}
