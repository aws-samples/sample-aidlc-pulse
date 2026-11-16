"""The AI-DLC audit event names Pulse depends on, in one place.

Every event name matched anywhere in Pulse must be listed in KNOWN_EVENTS; tests/test_events.py enforces that
by scanning the source, and checks KNOWN_EVENTS and RUN_LEVEL_PREFIXES against the engine's own
VALID_EVENT_TYPES (core/tools/aidlc-audit.ts upstream, <harness>/tools/aidlc-audit.ts when installed).
"""

from __future__ import annotations

# AI-DLC releases Pulse is tested against, as "major.minor". A project on another release still works,
# but the dashboard shows a warning because event names or file layouts may have changed.
TESTED_AIDLC_VERSIONS = ("2.11",)
TESTED_AIDLC_RELEASE = "2.11.4"

STAGE_STARTED = "STAGE_STARTED"
STAGE_AWAITING_APPROVAL = "STAGE_AWAITING_APPROVAL"
STAGE_REVISING = "STAGE_REVISING"
STAGE_COMPLETED = "STAGE_COMPLETED"
STAGE_SKIPPED = "STAGE_SKIPPED"
GATE_APPROVED = "GATE_APPROVED"
GATE_REJECTED = "GATE_REJECTED"
UNIT_STARTED = "UNIT_STARTED"
UNIT_RESUMED = "UNIT_RESUMED"
UNIT_PAUSED = "UNIT_PAUSED"
UNIT_COMPLETED = "UNIT_COMPLETED"
UNIT_SKIPPED = "UNIT_SKIPPED"
UNIT_MERGED = "UNIT_MERGED"
WORKFLOW_STARTED = "WORKFLOW_STARTED"
WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
WORKFLOW_PARKED = "WORKFLOW_PARKED"
ERROR_LOGGED = "ERROR_LOGGED"
SUBAGENT_DISPATCHED = "SUBAGENT_DISPATCHED"
SUBAGENT_COMPLETED = "SUBAGENT_COMPLETED"
REVIEW_REQUESTED = "REVIEW_REQUESTED"
REVIEW_COMPLETED = "REVIEW_COMPLETED"

KNOWN_EVENTS = frozenset({
    STAGE_STARTED, STAGE_AWAITING_APPROVAL, STAGE_REVISING, STAGE_COMPLETED, STAGE_SKIPPED,
    GATE_APPROVED, GATE_REJECTED,
    UNIT_STARTED, UNIT_RESUMED, UNIT_PAUSED, UNIT_COMPLETED, UNIT_SKIPPED, UNIT_MERGED,
    WORKFLOW_STARTED, WORKFLOW_COMPLETED, WORKFLOW_PARKED,
    ERROR_LOGGED, SUBAGENT_DISPATCHED, SUBAGENT_COMPLETED, REVIEW_REQUESTED, REVIEW_COMPLETED,
})

# Events that open a person's review of a stage, and events that send a stage back for rework.
GATE_OPENED = frozenset({STAGE_AWAITING_APPROVAL})
REWORK = frozenset({GATE_REJECTED, STAGE_REVISING})

# Event families that describe the whole run rather than one stage; they are never attributed to a stage.
RUN_LEVEL_PREFIXES = ("PHASE_", "SESSION_", "WORKFLOW_", "WORKSPACE_", "HEALTH_", "GUARDRAIL_")
