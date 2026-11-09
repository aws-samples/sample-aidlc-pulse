"""Units of work: the planned Units, and each Unit's progress through the per-unit Construction stages.

Plan, first one found:
  1. the fenced ```yaml edge block in inception/units-generation/unit-of-work-dependency.md (current format)
  2. <record>/runtime-graph.json  bolt_dag.units  (the engine's compiled copy of the same DAG)
  3. the "| Unit N (Name) | depends on |" matrix in the same markdown file (older runs)

Progress comes from UNIT_* audit events keyed by (Stage, Unit), the same receipts AI-DLC uses. A Unit is
"done" the way AI-DLC's own status line counts it: approved at its construction-unit checkpoint (and not
rejected since), or, when the run uses no checkpoints, every per-unit stage completed or skipped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DAG_FILE = Path("inception") / "units-generation" / "unit-of-work-dependency.md"
RUNTIME_GRAPH = "runtime-graph.json"
CONSTRUCTION_DIR = "construction"
PER_UNIT_MARKER = "unit-of-work"
CHECKPOINT = "construction-unit"
DONE = ("completed", "skipped")
CELL_FROM_EVENT = {
    "UNIT_STARTED": "in-progress",
    "UNIT_RESUMED": "in-progress",
    "UNIT_PAUSED": "paused",
    "UNIT_COMPLETED": "completed",
    "UNIT_SKIPPED": "skipped",
}
STATE_FIELDS = ("Construction Iteration", "Unit Ownership", "Construction Checkpoints")

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)
_LEGACY_ROW = re.compile(r"^\s*Unit\s+(\d+)\s*(?:\((.*?)\))?", re.I)
_LEGACY_REF = re.compile(r"(\d+)\s*[-–]\s*(\d+)|(\d+)")
_UNIT_NUMBER = re.compile(r"^(?:u(?:nit)?[\s_-]*)?0*(\d+)\b", re.I)


# --------------------------------------------------------------------------- plan


def load_plan(record_dir: Path) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """(units, source, format). Each unit: {name, label, kind, deps}."""
    text = _read(record_dir / DAG_FILE)
    if text and (units := parse_edge_block(text)):
        return units, DAG_FILE.as_posix(), "edge-block"
    if units := _runtime_graph_units(record_dir / RUNTIME_GRAPH):
        return units, RUNTIME_GRAPH, "runtime-graph"
    if text and (units := parse_legacy_matrix(text)):
        return units, DAG_FILE.as_posix(), "legacy-table"
    return [], None, None


def parse_edge_block(text: str) -> list[dict[str, Any]]:
    """Minimal reader for the edge block: `- name:`, `kind:`, `depends_on: [a, b]` or a `- a` list."""
    for block in _YAML_BLOCK.findall(text):
        if not re.search(r"^\s*units\s*:", block, re.M):
            continue
        units = _edge_units(block)
        if units:
            names = {u["name"] for u in units}
            for u in units:
                u["deps"] = [d for d in u["deps"] if d in names and d != u["name"]]
            return units
    return []


def _edge_units(block: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    in_deps = False
    for raw in block.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if m := re.match(r"^\s*-\s+name\s*:\s*['\"]?([^'\"\s]+)['\"]?\s*$", line):
            units.append({"name": m.group(1), "label": m.group(1), "kind": None, "deps": []})
            in_deps = False
        elif not units:
            continue
        elif m := re.match(r"^\s+kind\s*:\s*['\"]?([\w-]+)", line):
            units[-1]["kind"], in_deps = m.group(1), False
        elif m := re.match(r"^\s+depends_on\s*:\s*\[(.*)\]\s*$", line):
            units[-1]["deps"], in_deps = _names(m.group(1)), False
        elif re.match(r"^\s+depends_on\s*:\s*$", line):
            in_deps = True
        elif in_deps and (m := re.match(r"^\s+-\s+['\"]?([^'\"\s]+)['\"]?\s*$", line)):
            units[-1]["deps"].append(m.group(1))
    return units


def _names(inline: str) -> list[str]:
    return [n.strip().strip("'\"") for n in inline.split(",") if n.strip().strip("'\"")]


def _runtime_graph_units(path: Path) -> list[dict[str, Any]]:
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
        rows = graph["bolt_dag"]["units"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    units = [{"name": r["name"], "label": r["name"], "kind": r.get("kind"), "deps": [d for d in r.get("depends_on") or [] if isinstance(d, str)]}
             for r in rows if isinstance(r, dict) and isinstance(r.get("name"), str)]
    return units


def parse_legacy_matrix(text: str) -> list[dict[str, Any]]:
    """Older runs: a markdown table whose first column reads `Unit N (Name)` and second lists dependencies."""
    found: dict[int, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not (head := _LEGACY_ROW.match(cells[0])):
            continue
        num = int(head.group(1))
        if num in found:
            continue
        deps = [] if re.match(r"^\s*(none|—|-)?\s*$", cells[1], re.I) else _legacy_refs(cells[1])
        found[num] = {"name": f"unit-{num}", "label": head.group(2) or f"Unit {num}", "kind": None, "deps": [d for d in deps if d != num], "num": num}
    for u in found.values():
        u["deps"] = [f"unit-{d}" for d in u["deps"] if d in found]
    return [found[k] for k in sorted(found)]


def _legacy_refs(cell: str) -> list[int]:
    refs: set[int] = set()
    for m in _LEGACY_REF.finditer(cell):
        refs.update([int(m.group(3))] if m.group(3) else range(int(m.group(1)), int(m.group(2)) + 1))
    return sorted(refs)


# --------------------------------------------------------------------------- progress


def build_units(record_dir: Path, events: list[dict[str, Any]], stages: list[dict[str, Any]],
                sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    units, source, fmt = load_plan(record_dir)
    settings = _state_settings(sections)
    if not units:
        return {"units": [], "source": None, "format": None, "tracked": False, "perUnitStages": [], "summary": None, **settings}
    per_unit = [s for s in stages if (s.get("meta") or {}).get("forEach") == PER_UNIT_MARKER and s["decision"] == "EXECUTE"]
    resolve = _matcher(units)
    cells, history, paused_info, receipts = _cells_from_events(events, resolve)
    checkpoints = _checkpoint_done(events, resolve)
    if checkpoints is None and (settings["checkpoints"] or "").lower() == "enabled":
        checkpoints = {}  # the run uses checkpoints: no unit is done until its checkpoint is approved
    for u in units:
        _fill_unit(u, per_unit, cells, history, paused_info, checkpoints, record_dir)
    tracked = receipts > 0 or checkpoints is not None
    if not tracked:
        _fallback_from_stages(units, per_unit)
    return {
        "units": units, "source": source, "format": fmt, "tracked": tracked,
        "doneBy": "checkpoint" if checkpoints is not None else ("stages" if receipts else "stage-status"),
        "perUnitStages": [{"slug": s["slug"], "id": s.get("id"), "status": s["status"]} for s in per_unit],
        "summary": _summary(units), **settings,
    }


def _state_settings(sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    found = {}
    for fields in sections.values():
        for key in STATE_FIELDS:
            if key in fields and key not in found:
                found[key] = fields[key].strip() or None
    return {"iteration": found.get("Construction Iteration"), "ownership": found.get("Unit Ownership"),
            "checkpoints": found.get("Construction Checkpoints")}


def _matcher(units: list[dict[str, Any]]):
    """Event `Unit` value -> planned unit name. Exact name first; legacy plans also match by number (u3, Unit 3)."""
    exact = {u["name"]: u["name"] for u in units}
    by_num = {u["num"]: u["name"] for u in units if "num" in u}

    def resolve(value: str | None) -> str | None:
        if not value:
            return None
        value = value.strip()
        if value in exact:
            return exact[value]
        m = _UNIT_NUMBER.match(value)
        return by_num.get(int(m.group(1))) if m and by_num else None
    return resolve


def _cells_from_events(events, resolve):
    cells: dict[tuple[str, str], str] = {}
    history: dict[str, list[dict[str, Any]]] = {}
    paused: dict[str, dict[str, Any]] = {}
    receipts = 0
    for ev in events:
        name = ev["event"]
        unit = resolve(ev["fields"].get("Unit"))
        if not unit:
            continue
        stage = ev["fields"].get("Stage") or ev.get("stage")
        if name in CELL_FROM_EVENT and stage:
            status = CELL_FROM_EVENT[name]
            cells[(unit, stage)] = status
            receipts += 1
            history.setdefault(unit, []).append({"t": ev["timestamp"], "stage": stage, "status": status})
            if name == "UNIT_PAUSED":
                paused[unit] = {"stage": stage, "since": ev["timestamp"], "reason": ev["fields"].get("Reason"),
                                "nextAction": ev["fields"].get("Next Action"), "setAsideFor": ev["fields"].get("Set Aside For")}
            elif unit in paused and paused[unit]["stage"] == stage:
                paused.pop(unit)
        elif name == "GATE_REJECTED" and stage and (unit, stage) in cells:
            cells[(unit, stage)] = "in-progress"  # reopened for this unit
            history.setdefault(unit, []).append({"t": ev["timestamp"], "stage": stage, "status": "in-progress"})
        elif name == "UNIT_MERGED":
            history.setdefault(unit, []).append({"t": ev["timestamp"], "stage": None, "status": "merged"})
    return cells, history, paused, receipts


def _checkpoint_done(events, resolve) -> dict[str, str] | None:
    """Unit -> approval time, from construction-unit checkpoint gates (approve adds, reject removes). None if unused."""
    rows = [e for e in events if e["fields"].get("Checkpoint") == CHECKPOINT and e["event"] in ("GATE_APPROVED", "GATE_REJECTED")]
    merged = [e for e in events if e["event"] == "UNIT_MERGED"]
    if not rows and not merged:
        return None
    done: dict[str, str] = {}
    for e in sorted(rows + merged, key=lambda e: e["timestamp"] or ""):
        unit = resolve(e["fields"].get("Unit"))
        if not unit:
            continue
        if e["event"] == "GATE_REJECTED":
            done.pop(unit, None)
        else:
            done[unit] = e["timestamp"]
    return done


def _fill_unit(u, per_unit, cells, history, paused_info, checkpoints, record_dir: Path) -> None:
    unit_dir = record_dir / CONSTRUCTION_DIR / u["name"]
    u["stages"] = {}
    for st in per_unit:
        status = cells.get((u["name"], st["slug"]))
        if status is None:
            status = "pending" if not (unit_dir / st["slug"]).is_dir() else "files"
        u["stages"][st["slug"]] = status
    u["history"] = history.get(u["name"], [])
    u["paused"] = paused_info.get(u["name"])
    statuses = list(u["stages"].values())
    if checkpoints is not None:
        u["done"] = u["name"] in checkpoints
        u["doneAt"] = checkpoints.get(u["name"])
    else:
        u["done"] = bool(statuses) and all(s in DONE for s in statuses)
        u["doneAt"] = max((h["t"] for h in u["history"] if h["status"] == "completed"), default=None) if u["done"] else None
    u["startedAt"] = min((h["t"] for h in u["history"]), default=None)
    u["current"] = next((h["stage"] for h in reversed(u["history"]) if h["status"] == "in-progress" and cells.get((u["name"], h["stage"])) == "in-progress"), None)
    u["status"] = _unit_status(u, statuses)


def _unit_status(u: dict[str, Any], statuses: list[str]) -> str:
    if u["done"]:
        return "completed"
    if u["paused"]:
        return "paused"
    if any(s in ("in-progress", "completed", "skipped", "files") for s in statuses):
        return "in-progress"
    return "pending"


def _fallback_from_stages(units: list[dict[str, Any]], per_unit: list[dict[str, Any]]) -> None:
    """No unit receipts at all (older runs): a unit's cell takes its stage's own status."""
    for u in units:
        for st in per_unit:
            if u["stages"][st["slug"]] == "pending":
                u["stages"][st["slug"]] = {"completed": "completed", "skipped": "skipped"}.get(st["status"], "pending")
        statuses = list(u["stages"].values())
        u["done"] = bool(statuses) and all(s in DONE for s in statuses)
        u["status"] = _unit_status(u, statuses)


def _summary(units: list[dict[str, Any]]) -> dict[str, Any]:
    count = lambda st: sum(1 for u in units if u["status"] == st)
    current = next(({"unit": u["name"], "label": u["label"], "stage": u["current"]} for u in units if u.get("current")), None)
    done = count("completed")
    return {
        "total": len(units), "done": done, "inProgress": count("in-progress"), "paused": count("paused"), "pending": count("pending"),
        "percent": round(100 * done / len(units)) if units else 0,
        "current": current,
        "pausedUnits": [{"unit": u["name"], "label": u["label"], **u["paused"]} for u in units if u.get("paused")],
    }


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
