"""Builds small synthetic AI-DLC projects on disk, in the layout AI-DLC 2.11 writes.

Nothing here comes from a real run, so the tests carry no project data.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

SPACE = "default"
INTENT = "261103-sample-api"
RECORD_ID = f"{SPACE}/{INTENT}"

# (slug, number, name, phase, for_each)
STAGES = [
    ("workspace-scaffold", "0.1", "Workspace Scaffold", "initialization", None),
    ("requirements-analysis", "2.3", "Requirements Analysis", "inception", None),
    ("units-generation", "2.7", "Units Generation", "inception", None),
    ("functional-design", "3.1", "Functional Design", "construction", "unit-of-work"),
    ("code-generation", "3.5", "Code Generation", "construction", "unit-of-work"),
    ("build-and-test", "3.6", "Build and Test", "construction", None),
]

STATE = """# AI-DLC State

## Project Information
- **Scope**: classic
- **Active Agent**: aidlc-architect-agent

## Current Status
- **Current Stage**: functional-design
- **Status**: Running

## Stage Progress

### INITIALIZATION PHASE
- [x] workspace-scaffold — EXECUTE

### INCEPTION PHASE
- [x] requirements-analysis — EXECUTE
- [x] units-generation — EXECUTE

### CONSTRUCTION PHASE
- [-] functional-design — EXECUTE
- [ ] code-generation — EXECUTE
- [ ] build-and-test — SKIP
"""

DAG = """# Unit of Work Dependency

## Machine-Readable Edge Block

```yaml
units:
  - name: u1-items
    kind: service
    depends_on: []
  - name: u2-tags
    kind: service
    depends_on: [u1-items]
```
"""

QUESTIONS = """# Requirements Analysis Questions

## Q1. [Product manager] What does an item hold?

A. A name and a URL (Recommended)
B. A name only
X. Other (please specify)

[Answer]: A (chosen as recommended) **Mode:** chat

## Q2. [Product manager] Are duplicates allowed?

A. Yes (Recommended)
B. No

[Answer]:
"""


def event(ts: str, name: str, stage: str | None = None, **fields: str) -> dict[str, Any]:
    return {"ts": ts, "event": name, "stage": stage, "fields": fields}


def audit_text(events: list[dict[str, Any]]) -> str:
    blocks = []
    for e in events:
        lines = [f"## {e['event'].replace('_', ' ').title()}", f"**Timestamp**: {e['ts']}", f"**Event**: {e['event']}"]
        if e["stage"]:
            lines.append(f"**Stage**: {e['stage']}")
        lines += [f"**{k.replace('_', ' ')}**: {v}" for k, v in e["fields"].items()]
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks) + "\n"


DEFAULT_EVENTS = [
    event("2026-11-03T10:00:00Z", "WORKFLOW_STARTED"),
    event("2026-11-03T10:00:00Z", "STAGE_STARTED", "workspace-scaffold", Agent="orchestrator"),
    event("2026-11-03T10:00:01Z", "STAGE_COMPLETED", "workspace-scaffold"),
    event("2026-11-03T10:01:00Z", "STAGE_STARTED", "requirements-analysis", Agent="aidlc-product-lead-agent"),
    event("2026-11-03T10:05:00Z", "STAGE_AWAITING_APPROVAL", "requirements-analysis"),
    event("2026-11-03T10:07:00Z", "GATE_REJECTED", "requirements-analysis"),
    event("2026-11-03T10:07:00Z", "STAGE_REVISING", "requirements-analysis"),
    event("2026-11-03T10:09:00Z", "STAGE_AWAITING_APPROVAL", "requirements-analysis"),
    event("2026-11-03T10:10:00Z", "GATE_APPROVED", "requirements-analysis"),
    event("2026-11-03T10:10:00Z", "STAGE_COMPLETED", "requirements-analysis"),
    event("2026-11-03T10:10:00Z", "STAGE_STARTED", "units-generation", Agent="aidlc-architect-agent"),
    event("2026-11-03T10:14:00Z", "STAGE_AWAITING_APPROVAL", "units-generation"),
    event("2026-11-03T10:15:00Z", "GATE_APPROVED", "units-generation"),
    event("2026-11-03T10:15:00Z", "STAGE_COMPLETED", "units-generation"),
    event("2026-11-03T10:15:00Z", "STAGE_STARTED", "functional-design", Agent="aidlc-architect-agent"),
    event("2026-11-03T10:16:00Z", "UNIT_STARTED", "functional-design", Unit="u1-items"),
    event("2026-11-03T10:17:00Z", "ARTIFACT_CREATED"),
    event("2026-11-03T10:19:00Z", "UNIT_COMPLETED", "functional-design", Unit="u1-items"),
    event("2026-11-03T10:20:00Z", "UNIT_STARTED", "code-generation", Unit="u1-items"),
    event("2026-11-03T10:23:00Z", "UNIT_PAUSED", "code-generation", Unit="u1-items", Reason="npm install failed"),
]


class Project:
    """A temp AI-DLC project. Use as a context manager, or call cleanup()."""

    def __init__(self, harness: str = ".kiro", version: str = "2.11.2", stage_graph: bool = True,
                 scope_grid: bool = True, events: list[dict[str, Any]] | None = None) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pulse-test-")).resolve()
        self.harness = self.root / harness
        self.record = self.root / "aidlc" / "spaces" / SPACE / "intents" / INTENT
        self._harness(version, stage_graph, scope_grid)
        self._record(DEFAULT_EVENTS if events is None else events)

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel: str, text: str, base: Path | None = None) -> Path:
        path = (base or self.record) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def add_harness(self, name: str) -> Path:
        other = self.root / name
        shutil.copytree(self.harness, other)
        return other

    def _harness(self, version: str, stage_graph: bool, scope_grid: bool) -> None:
        data = self.harness / "tools" / "data"
        data.mkdir(parents=True)
        (data / "aidlc-stamp.json").write_text(json.dumps({"schemaVersion": 1, "frameworkVersion": version}))
        if stage_graph:
            graph = [{"slug": s, "number": n, "name": nm, "phase": ph, "lead_agent": "aidlc-architect-agent",
                      "support_agents": [], "produces": [], **({"for_each": fe} if fe else {})}
                     for s, n, nm, ph, fe in STAGES]
            (data / "stage-graph.json").write_text(json.dumps(graph))
        if scope_grid:
            grid = {"classic": {"stages": {s: "EXECUTE" for s, *_ in STAGES}}}
            (data / "scope-grid.json").write_text(json.dumps(grid))
        for slug, _, _, phase, for_each in STAGES:
            fm = f"---\nslug: {slug}\nphase: {phase}\nlead_agent: aidlc-architect-agent\n" + (f"for_each: {for_each}\n" if for_each else "") + "---\n"
            self.write(f"aidlc-common/stages/{phase}/{slug}.md", fm, self.harness)
        self.write("agents/aidlc-architect-agent.json", json.dumps({"description": "Architect"}), self.harness)

    def _record(self, events: list[dict[str, Any]]) -> None:
        self.write("aidlc-state.md", STATE)
        self.write("audit/host-abc123.md", audit_text(events))
        self.write("inception/units-generation/unit-of-work-dependency.md", DAG)
        self.write("inception/requirements-analysis/requirements.md", "# Requirements\n")
        self.write("inception/requirements-analysis/requirements-analysis-questions.md", QUESTIONS)
        self.write("construction/u1-items/functional-design/business-logic.md", "# Logic\n")
