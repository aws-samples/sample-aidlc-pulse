"""Read-only parser that turns an AI-DLC v2 project's files into a status model.

Sources (all read, never written):
  * <harness>/tools/data/stage-graph.json     stage order, numbers, names and metadata (lead, reviewer, ...)
  * <harness>/tools/data/scope-grid.json      stage order fallback (older installs)
  * <harness>/aidlc-common/stages/**.md       stage frontmatter, when there is no stage graph
  * <harness>/tools/data/aidlc-stamp.json     installed AI-DLC release
  * aidlc/spaces/<space>/intents/<intent>/    v2 intent records (aidlc-state.md, audit/, artifacts)
  * aidlc/spaces/<space>/intents/audit/       space-level audit shard (events not bound to one intent)
  * aidlc-docs/                               legacy flat record (pre-workspace layout)
  * aidlc/.aidlc-sessions/                    current session and pending engine questions

Files are cached by (mtime, size), so a poll only re-reads and re-parses what changed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import decisions
import events as ev_names
import insights
import units
from version import PULSE_VERSION

log = logging.getLogger("aidlc-pulse")

HARNESS_DIRS = (".kiro", ".claude", ".codex", ".cursor", ".opencode")
STAGE_GRAPH = Path("tools") / "data" / "stage-graph.json"
STAMP_FILE = Path("tools") / "data" / "aidlc-stamp.json"
CACHE_MAX_BYTES = 128 * 1024 * 1024
PHASES = ("initialization", "ideation", "inception", "construction", "operation")
LEGACY_RECORD_DIR = "aidlc-docs"
LEGACY_RECORD_ID = "legacy"
STATE_FILE = "aidlc-state.md"
AUDIT_FILE = "audit.md"
AUDIT_DIR = "audit"
SPACES_DIR = Path("aidlc") / "spaces"
SESSIONS_DIR = Path("aidlc") / ".aidlc-sessions"
SCOPE_GRID = Path("tools") / "data" / "scope-grid.json"
STAGES_DIR = Path("aidlc-common") / "stages"
REGISTRY_FILE = "intents.json"
# Older state files use the pre-rename slug for 2.6.
STAGE_ALIASES = {"application-design": "domain-design"}
CHECKBOX_STATUS = {
    " ": "pending",
    "-": "in-progress",
    "?": "awaiting-approval",
    "R": "revising",
    "x": "completed",
    "X": "completed",
    "S": "skipped",
}
RECENT_EVENT_LIMIT = 60
MAX_AUDIT_EVENTS = 5000
AGENTS_DIR = "agents"
AGENT_TIERS = Path("tools") / "data" / "agent-tiers.json"
ORCHESTRATOR = "orchestrator"
ACTIVE_STATUSES = ("in-progress", "revising", "awaiting-approval")
# Audit fields that name the agent an event belongs to, in priority order.
AGENT_FIELDS = ("Agent", "Agent Type", "Reviewer", "Dispatch Agent")
RUN_LEVEL_PREFIXES = ev_names.RUN_LEVEL_PREFIXES
MAX_ARTIFACT_BYTES = 512_000
MAX_TEXT_BYTES = 16 * 1024 * 1024  # cap on any single state, audit or session file read
MAX_PATH_SEGMENT = 255
ARTIFACT_SUFFIXES = (".md", ".json", ".yaml", ".yml", ".txt", ".html", ".mmd")
CONSTRUCTION_PHASE = "construction"

_BULLET = re.compile(r"^- \*\*(.+?)\*\*:\s*(.*)$")
_STAGE_LINE = re.compile(r"^- \[(.)\]\s+([A-Za-z0-9-]+)\s+[—-]+\s+(EXECUTE|SKIP)\b", re.I)
_PHASE_HEAD = re.compile(r"^###\s+(\w+)\s+PHASE", re.I)
_SECTION_HEAD = re.compile(r"^##\s+(.+?)\s*$")
_FIELD = re.compile(r"^\*\*(.+?)\*\*:\s*(.*)$")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.S)
# Path segments may hold any printable character (spaces, accents, CJK, ...) except separators and controls.
_UNSAFE_SEGMENT = re.compile(r"[\x00-\x1f\x7f/\\]")
_VERSION = re.compile(r"^(\d+)\.(\d+)")


class RecordNotFound(LookupError):
    """Raised when a record id does not match any discovered record."""


class ArtifactRefused(PermissionError):
    """Raised when an artifact path is outside its record or not a viewable file."""


# --------------------------------------------------------------------------- helpers


class FileCache:
    """Values derived from a file, reused while the file's (mtime_ns, size) is unchanged. LRU, capped by bytes."""

    def __init__(self, max_bytes: int) -> None:
        self._max = max_bytes
        self._used = 0
        self._rows: OrderedDict[tuple[str, str], tuple[tuple[int, int], Any, int]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path, kind: str, build: Callable[[Path], Any]) -> Any:
        stat = path.stat()  # OSError propagates: callers already handle missing files
        key, sig = (str(path), kind), (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            row = self._rows.get(key)
            if row and row[0] == sig:
                self._rows.move_to_end(key)
                return row[1]
        value = build(path)
        self._put(key, sig, value, stat.st_size)
        return value

    def _put(self, key: tuple[str, str], sig: tuple[int, int], value: Any, size: int) -> None:
        with self._lock:
            old = self._rows.pop(key, None)
            self._used -= old[2] if old else 0
            if size > self._max:
                return
            self._rows[key] = (sig, value, size)
            self._used += size
            while self._used > self._max and self._rows:
                self._used -= self._rows.popitem(last=False)[1][2]

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._used = 0


CACHE = FileCache(CACHE_MAX_BYTES)


def _read_text(path: Path) -> str:
    """File text via the cache; at most MAX_TEXT_BYTES so one huge project file can't exhaust memory."""
    return CACHE.get(path, "text", _read_text_uncached)


def _read_text_uncached(path: Path) -> str:
    with path.open("rb") as fh:
        data = fh.read(MAX_TEXT_BYTES + 1)
    if len(data) > MAX_TEXT_BYTES:
        log.warning(json.dumps({"msg": "file_truncated", "path": str(path), "limitBytes": MAX_TEXT_BYTES}))
        data = data[:MAX_TEXT_BYTES]
    return data.decode("utf-8", errors="replace")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except (OSError, json.JSONDecodeError):
        return None


def _mtime_iso(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse the flat YAML subset AI-DLC uses: scalars and `- item` block lists."""
    match = _FRONTMATTER.match(text)
    result: dict[str, Any] = {}
    if not match:
        return result
    key: str | None = None
    for line in match.group(1).splitlines():
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key is not None:
            if not isinstance(result.get(key), list):
                result[key] = []
            result[key].append(item.group(1).strip())
            continue
        pair = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if pair:
            key = pair.group(1)
            result[key] = _scalar(pair.group(2))
    return result


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        return [v.strip() for v in value[1:-1].split(",") if v.strip()]
    return value


# --------------------------------------------------------------------------- harness / catalog


def find_harnesses(root: Path) -> list[Path]:
    """Every installed AI-DLC harness dir, in HARNESS_DIRS order."""
    found = []
    for name in HARNESS_DIRS:
        candidate = root / name
        if (candidate / STAGE_GRAPH).is_file() or (candidate / STAGES_DIR).is_dir() or (candidate / SCOPE_GRID).is_file():
            found.append(candidate)
    return found


def select_harness(root: Path, preferred: str | None = None) -> tuple[Path | None, list[str]]:
    """(harness, warnings). `preferred` is a harness dir name such as ".claude"; otherwise the first found.

    AI-DLC records don't say which harness ran them, so with several installed the choice is reported."""
    found = find_harnesses(root)
    if preferred:
        match = next((h for h in found if h.name == preferred), None)
        if match is None:
            fallback = found[0].name if found else "none"
            return (found[0] if found else None), [f"Harness {preferred} not found in this project; using {fallback}."]
        return match, []
    if len(found) > 1:
        names = ", ".join(h.name for h in found)
        warning = (f"Several AI-DLC harnesses are installed ({names}); using {found[0].name}. "
                   "Start Pulse with --harness to pick another.")
        return found[0], [warning]
    return (found[0] if found else None), []


def find_harness(root: Path) -> Path | None:
    return select_harness(root)[0]


def aidlc_version(harness: Path | None) -> str | None:
    stamp = _read_json(harness / STAMP_FILE) if harness else None
    version = stamp.get("frameworkVersion") if isinstance(stamp, dict) else None
    return version if isinstance(version, str) and version else None


def version_warning(version: str | None) -> str | None:
    if version is None:
        return None
    m = _VERSION.match(version)
    if m and f"{m.group(1)}.{m.group(2)}" in ev_names.TESTED_AIDLC_VERSIONS:
        return None
    return (f"This project uses AI-DLC {version}; Pulse is tested against {ev_names.TESTED_AIDLC_RELEASE}. "
            "Some figures may be missing or wrong if event names or file layouts changed.")


def load_scope_grid(harness: Path | None) -> dict[str, dict[str, str]]:
    if harness is None:
        return {}
    data = _read_json(harness / SCOPE_GRID) or {}
    return {name: body.get("stages", {}) for name, body in data.items() if isinstance(body, dict)}


def load_stage_graph(harness: Path | None) -> list[dict[str, Any]]:
    """The engine's compiled stage list (canonical order), or [] when absent or malformed."""
    data = _read_json(harness / STAGE_GRAPH) if harness else None
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and isinstance(row.get("slug"), str)]


def load_stage_catalog(harness: Path | None, graph: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Stage slug -> metadata. The stage graph wins; stage-file frontmatter fills in when there is no graph."""
    graph = load_stage_graph(harness) if graph is None else graph
    if graph:
        return {row["slug"]: _stage_meta(row, None) for row in graph}
    catalog: dict[str, dict[str, Any]] = {}
    if harness is None or not (harness / STAGES_DIR).is_dir():
        return catalog
    for path in (harness / STAGES_DIR).rglob("*.md"):
        fm = parse_frontmatter(_read_text(path))
        slug = str(fm.get("slug") or path.stem)
        catalog[slug] = _stage_meta(fm, path)
    return catalog


def _stage_meta(fm: dict[str, Any], path: Path | None) -> dict[str, Any]:
    def as_list(key: str) -> list[str]:
        value = fm.get(key)
        return [str(v) for v in value] if isinstance(value, list) else ([str(value)] if value else [])

    return {
        "phase": str(fm.get("phase") or (path.parent.name if path else "")).lower(),
        "name": fm.get("name") or None,
        "number": str(fm["number"]) if fm.get("number") else None,
        "lead": fm.get("lead_agent") or None,
        "support": as_list("support_agents"),
        "reviewer": fm.get("reviewer") or None,
        "execution": fm.get("execution") or None,
        "condition": fm.get("condition") or None,
        "produces": as_list("produces"),
        "forEach": fm.get("for_each") or None,
    }


def canonical_order(grid: dict[str, dict[str, str]], catalog: dict[str, dict[str, Any]],
                    graph: list[dict[str, Any]] | None = None) -> tuple[list[str], str]:
    """(stage order, source). Stage graph first, then the compiled scope grid, then phase order + slug."""
    if graph:
        return [row["slug"] for row in graph], "stage-graph"
    for stages in grid.values():
        if stages:
            return list(stages.keys()), "scope-grid"
    guessed = sorted(catalog, key=lambda s: (PHASES.index(catalog[s]["phase"]) if catalog[s]["phase"] in PHASES else 99, s))
    return guessed, "guessed"


def order_warning(source: str, has_catalog: bool) -> str | None:
    if source != "guessed" or not has_catalog:
        return None
    return ("Stage order is a guess (phase, then name): this install has no tools/data/stage-graph.json "
            "or scope-grid.json. Stage numbers may not match AI-DLC's.")


def stage_ids(order: list[str], catalog: dict[str, dict[str, Any]]) -> dict[str, str]:
    """The familiar `<phase>.<n>` ids (0.1, 1.1, ...): the stage graph's own number, else counted from order."""
    ids: dict[str, str] = {}
    counters: dict[str, int] = {}
    for slug in order:
        meta = catalog.get(slug, {})
        phase = meta.get("phase", "")
        if phase not in PHASES:
            continue
        counters[phase] = counters.get(phase, 0) + 1
        ids[slug] = meta.get("number") or f"{PHASES.index(phase)}.{counters[phase]}"
    return ids


# --------------------------------------------------------------------------- records


def discover_records(root: Path) -> list[dict[str, Any]]:
    """Every record that has an aidlc-state.md, newest first."""
    records = _v2_records(root)
    legacy = root / LEGACY_RECORD_DIR / STATE_FILE
    if legacy.is_file():
        records.append(_record_summary(LEGACY_RECORD_ID, "aidlc-docs (legacy layout)", legacy.parent, "legacy", None, None))
    return sorted(records, key=lambda r: r["updatedAt"], reverse=True)


def _v2_records(root: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    spaces = root / SPACES_DIR
    if not spaces.is_dir():
        return found
    for space in sorted(p for p in spaces.iterdir() if p.is_dir()):
        registry = _registry_by_dir(space / "intents")
        for state in sorted((space / "intents").glob(f"*/{STATE_FILE}")):
            intent = state.parent.name
            row = registry.get(intent, {})
            found.append(_record_summary(f"{space.name}/{intent}", intent, state.parent, "intent", space.name, row))
    return found


def _registry_by_dir(intents_dir: Path) -> dict[str, dict[str, Any]]:
    rows = _read_json(intents_dir / REGISTRY_FILE)
    if isinstance(rows, dict):
        rows = rows.get("intents", [])
    if not isinstance(rows, list):
        return {}
    by_dir: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("slug"):
            name = row.get("dirName") or f"{row['slug']}-{str(row.get('uuid', ''))[:8]}"
            by_dir[str(name)] = row
    return by_dir


def _record_summary(
    rid: str, label: str, path: Path, kind: str, space: str | None, row: dict[str, Any] | None
) -> dict[str, Any]:
    state = parse_state(_read_text(path / STATE_FILE))
    status = state["sections"].get("Current Status", {})
    info = state["sections"].get("Project Information", {})
    return {
        "id": rid,
        "label": label,
        "kind": kind,
        "space": space,
        "path": str(path),
        "scope": (row or {}).get("scope") or info.get("Scope"),
        "status": status.get("Status") or (row or {}).get("status"),
        "registryStatus": (row or {}).get("status"),
        "updatedAt": _mtime_iso(path / STATE_FILE),
    }


def resolve_record(root: Path, record_id: str | None) -> dict[str, Any]:
    records = discover_records(root)
    if not records:
        raise RecordNotFound("No aidlc-state.md found in this project yet.")
    if not record_id:
        return records[0]
    for record in records:
        if record["id"] == record_id:
            return record
    raise RecordNotFound(f"Unknown record: {record_id}")


# --------------------------------------------------------------------------- state file


def parse_state(text: str) -> dict[str, Any]:
    """Split aidlc-state.md into `- **Key**: value` sections and stage checkbox lines."""
    sections: dict[str, dict[str, str]] = {}
    stages: list[dict[str, str]] = []
    section, phase = "", ""
    for line in text.splitlines():
        if head := _SECTION_HEAD.match(line):
            section = head.group(1)
            continue
        if ph := _PHASE_HEAD.match(line):
            phase = ph.group(1).lower()
            continue
        if stage := _STAGE_LINE.match(line):
            stages.append(_stage_row(stage, phase))
        elif bullet := _BULLET.match(line):
            sections.setdefault(section, {})[bullet.group(1)] = bullet.group(2).strip()
    return {"sections": sections, "stages": stages}


def _stage_row(match: re.Match[str], phase: str) -> dict[str, str]:
    raw = match.group(2)
    return {
        "slug": STAGE_ALIASES.get(raw, raw),
        "stateSlug": raw,
        "phase": phase,
        "mark": match.group(1),
        "status": CHECKBOX_STATUS.get(match.group(1), "unknown"),
        "decision": match.group(3).upper(),
    }


# --------------------------------------------------------------------------- audit


def audit_files(record_dir: Path) -> list[Path]:
    files = sorted((record_dir / AUDIT_DIR).glob("*.md")) if (record_dir / AUDIT_DIR).is_dir() else []
    flat = record_dir / AUDIT_FILE
    return files + ([flat] if flat.is_file() else [])


def space_audit_files(record: dict[str, Any]) -> list[Path]:
    """The space-level shard (`<space>/intents/audit/`): the engine writes there when no single intent is
    selected (before the first intent, or several intents and no cursor). Not tied to this record."""
    if record.get("kind") != "intent":
        return []
    shard_dir = Path(record["path"]).parent / AUDIT_DIR
    return sorted(shard_dir.glob("*.md")) if shard_dir.is_dir() else []


def parse_audit(paths: list[Path], source: str | None = None, known_stages: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Each `---`-separated block -> {title, timestamp, event, fields}, oldest first."""
    events: list[dict[str, Any]] = []
    for path in paths:
        try:
            blocks = CACHE.get(path, "audit", _parse_audit_file)
        except OSError:
            continue
        # Fresh top-level dicts: stage inference below writes to them, the cached copies stay pristine.
        events.extend({**e, "source": source} if source else dict(e) for e in blocks)
    events.sort(key=_event_sort_key)
    _infer_stages(events, known_stages)
    return events


def _parse_audit_file(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"^---\s*$", _read_text_uncached(path), flags=re.M)
    return [event for block in blocks if (event := _audit_block(block))]


def _event_sort_key(ev: dict[str, Any]) -> tuple[int, float]:
    """Chronological, by parsed time (offsets and fractional seconds included); undated events sort first."""
    ts = insights.parse_ts(ev.get("timestamp"))
    return (1, ts.timestamp()) if ts else (0, 0.0)


def _infer_stages(events: list[dict[str, Any]], known: frozenset[str] = frozenset()) -> None:
    """Give events with no Stage field a stage, most reliable source first:

    1. a `Stage slug` field (sensor events)               -> stageFrom "field"
    2. the `File` path inside the record (artifact events) -> stageFrom "path" (+ unit)
    3. the stage the event's `Unit` is in right now       -> stageInferred
    4. the one stage running at the time                  -> stageInferred
    When several units are in different stages and nothing above applies, the event is left unattributed
    with stageAmbiguous, rather than credited to whichever stage started last. Run-level events stay unattributed.
    """
    current: str | None = None  # last stage started (stage-level)
    unit_at: dict[str, str] = {}  # unit -> stage it is working in
    for ev in events:
        name, fields = ev["event"], ev.get("fields") or {}
        if not ev["stage"]:
            _stage_from_event(ev, known)
        if name == ev_names.STAGE_STARTED and ev["stage"]:
            current = ev["stage"]
        _track_unit(unit_at, name, fields.get("Unit"), ev["stage"])
        if not ev["stage"] and not name.startswith(RUN_LEVEL_PREFIXES):
            _guess_stage(ev, fields.get("Unit"), unit_at, current)
        if name in (ev_names.WORKFLOW_COMPLETED, ev_names.WORKFLOW_PARKED):
            current = None
            unit_at.clear()


def _stage_from_event(ev: dict[str, Any], known: frozenset[str]) -> None:
    fields = ev.get("fields") or {}
    if slug := (fields.get("Stage slug") or "").strip():
        ev["stage"], ev["stageFrom"] = STAGE_ALIASES.get(slug, slug), "field"
        return
    stage, unit = _stage_from_path(fields.get("File") or "", known)
    if stage:
        ev["stage"], ev["stageFrom"] = stage, "path"
        if unit and not fields.get("Unit"):
            ev["unit"] = unit


def _stage_from_path(path: str, known: frozenset[str]) -> tuple[str | None, str | None]:
    """`.../intents/<intent>/<phase>/<slug>/x.md` -> (slug, None); `.../construction/<unit>/<slug>/x.md` -> (slug, unit)."""
    parts = path.replace("\\", "/").split("/")
    if "intents" not in parts:
        return None, None
    rest = parts[parts.index("intents") + 2:]  # skip "intents" and the intent dir name
    if len(rest) < 3 or rest[0] not in PHASES:
        return None, None
    dirs = rest[1:-1]  # directories between the phase and the file name
    if known:
        for i, seg in enumerate(dirs):
            slug = STAGE_ALIASES.get(seg, seg)
            if slug in known:
                return slug, (dirs[i - 1] if i and rest[0] == CONSTRUCTION_PHASE else None)
        return None, None
    if rest[0] == CONSTRUCTION_PHASE and len(dirs) >= 2:
        return STAGE_ALIASES.get(dirs[1], dirs[1]), dirs[0]
    return STAGE_ALIASES.get(dirs[0], dirs[0]), None


def _track_unit(unit_at: dict[str, str], name: str, unit: str | None, stage: str | None) -> None:
    if not unit:
        return
    if name in (ev_names.UNIT_STARTED, ev_names.UNIT_RESUMED) and stage:
        unit_at[unit] = stage
    elif name in (ev_names.UNIT_COMPLETED, ev_names.UNIT_SKIPPED, ev_names.UNIT_PAUSED):
        unit_at.pop(unit, None)


def _guess_stage(ev: dict[str, Any], unit: str | None, unit_at: dict[str, str], current: str | None) -> None:
    active = set(unit_at.values())
    if unit and unit in unit_at:
        guess = unit_at[unit]
    elif len(active) == 1:
        guess = next(iter(active))
    elif len(active) > 1:
        ev["stageAmbiguous"] = sorted(active)
        return
    else:
        guess = current
    if guess:
        ev["stage"], ev["stageInferred"] = guess, True


def _audit_block(block: str) -> dict[str, Any] | None:
    title, fields = None, {}
    for line in block.strip().splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
        elif match := _FIELD.match(line.strip()):
            fields.setdefault(match.group(1), match.group(2).strip())  # first value wins
    if "Event" not in fields:
        return None
    return {
        "title": title,
        "event": fields.pop("Event"),
        "timestamp": fields.pop("Timestamp", None),
        "stage": STAGE_ALIASES.get(fields.get("Stage", ""), fields.get("Stage")),
        "agent": next((fields[k] for k in AGENT_FIELDS if fields.get(k) and fields[k] != "unknown"), None),
        "fields": fields,
    }


def stage_timeline(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-stage start/completion/gate counts derived from audit events."""
    timeline: dict[str, dict[str, Any]] = {}
    for ev in events:
        slug = ev.get("stage")
        if not slug:
            continue
        entry = timeline.setdefault(slug, {"startedAt": None, "completedAt": None, "gates": 0, "approvals": 0, "revisions": 0, "agents": []})
        if ev.get("agent") and ev["agent"] not in entry["agents"]:
            entry["agents"].append(ev["agent"])
        _apply_event(entry, ev)
    return timeline


def _apply_event(entry: dict[str, Any], ev: dict[str, Any]) -> None:
    name, ts = ev["event"], ev["timestamp"]
    if name == ev_names.STAGE_STARTED and not entry["startedAt"]:
        entry["startedAt"] = ts
    elif name == ev_names.STAGE_COMPLETED:
        entry["completedAt"] = ts
    elif name in ev_names.GATE_OPENED:
        entry["gates"] += 1
    elif name == ev_names.GATE_APPROVED:
        entry["approvals"] += 1
    elif name == ev_names.GATE_REJECTED:  # one per change request (STAGE_REVISING usually follows it)
        entry["revisions"] += 1


# --------------------------------------------------------------------------- artifacts


def list_artifacts(record_dir: Path, phase: str, slug: str, state_slug: str) -> list[dict[str, Any]]:
    """Files under `<phase>/<slug>/`, plus `construction/<unit>/<slug>/` for per-unit Construction stages."""
    out: list[dict[str, Any]] = []
    for candidate in dict.fromkeys((slug, state_slug)):
        dirs = [(record_dir / phase / candidate, None)]
        if phase == CONSTRUCTION_PHASE and (record_dir / phase).is_dir():
            dirs += [(unit / candidate, unit.name) for unit in sorted((record_dir / phase).iterdir())
                     if unit.is_dir() and not unit.name.startswith(".") and unit.name != candidate]
        for stage_dir, unit in dirs:
            if stage_dir.is_dir():
                out.extend(_artifact_rows(record_dir, stage_dir, unit))
    return out


def _artifact_rows(record_dir: Path, stage_dir: Path, unit: str | None) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(stage_dir.rglob("*")):
        if any(p.startswith(".") for p in path.relative_to(stage_dir).parts):
            continue  # hidden files can't be opened (see _safe_segment), so don't list them
        if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES:
            row = {"path": path.relative_to(record_dir).as_posix(), "name": path.name, "size": path.stat().st_size}
            if unit:
                row["unit"] = unit
            rows.append(row)
    return rows


def read_artifact(root: Path, record_id: str | None, rel_path: str) -> dict[str, Any]:
    """Return one artifact's text. Refuses anything that resolves outside the record.

    Segments may contain spaces and non-ASCII letters; separators, control characters, `.`/`..`, and
    hidden (dot) segments are refused, then the resolved path (symlinks followed) must stay in the record."""
    record_dir = Path(resolve_record(root, record_id)["path"]).resolve()
    parts = rel_path.split("/")
    if not rel_path or any(not _safe_segment(p) for p in parts):
        raise ArtifactRefused("Invalid artifact path.")
    target = (record_dir / Path(*parts)).resolve()
    if not target.is_relative_to(record_dir) or not target.is_file():
        raise ArtifactRefused("Artifact not found in this record.")
    if target.suffix.lower() not in ARTIFACT_SUFFIXES:
        raise ArtifactRefused("File type not viewable.")
    data = target.read_bytes()[:MAX_ARTIFACT_BYTES]
    return {"path": rel_path, "truncated": target.stat().st_size > MAX_ARTIFACT_BYTES, "text": data.decode("utf-8", "replace")}


def _safe_segment(part: str) -> bool:
    return (0 < len(part) <= MAX_PATH_SEGMENT and part == part.strip() and not part.startswith(".")
            and not _UNSAFE_SEGMENT.search(part))


# --------------------------------------------------------------------------- agents


def load_roster(harness: Path | None) -> dict[str, dict[str, Any]]:
    """Installed AI-DLC agents (<harness>/agents/aidlc-*.json) with their model tier."""
    if harness is None:
        return {}
    tiers = _read_json(harness / AGENT_TIERS) or {}
    names = sorted({p.stem for p in (harness / AGENTS_DIR).glob("aidlc-*.json")}) if (harness / AGENTS_DIR).is_dir() else []
    return {n: {"name": n, "tier": tiers.get(_short_name(n)), "description": _agent_description(harness, n)} for n in names}


def _short_name(agent: str) -> str:
    return agent.removeprefix("aidlc-").removesuffix("-agent")


def _agent_description(harness: Path, name: str) -> str | None:
    data = _read_json(harness / AGENTS_DIR / f"{name}.json")
    return data.get("description") if isinstance(data, dict) else None


def agent_activity(
    roster: dict[str, dict[str, Any]], stages: list[dict[str, Any]], events: list[dict[str, Any]], active_agent: str | None
) -> list[dict[str, Any]]:
    agents = {name: _blank_agent(name, meta) for name, meta in roster.items()}
    for stage in stages:
        _plan_from_stage(agents, roster, stage)
    for ev in events:
        _apply_agent_event(agents, roster, ev)
    for agent in agents.values():
        agent["state"] = _agent_state(agent, stages, active_agent)
    order = {"working": 0, "awaiting-approval": 1, "contributed": 2, "planned": 3, "no-record": 4, "idle": 5}
    return sorted(agents.values(), key=lambda a: (order[a["state"]], a["name"]))


def _blank_agent(name: str, meta: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "name": name, "tier": (meta or {}).get("tier"), "description": (meta or {}).get("description"),
        "leads": [], "supports": [], "reviews": [], "ran": [], "dispatches": 0, "completions": 0,
        "lastDispatch": None, "lastCompletion": None, "firstSeen": None, "lastSeen": None, "events": 0,
    }


def _agent(agents: dict[str, dict[str, Any]], roster: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    return agents.setdefault(name, _blank_agent(name, roster.get(name)))


def _plan_from_stage(agents, roster, stage: dict[str, Any]) -> None:
    if stage["decision"] != "EXECUTE":
        return
    meta = stage.get("meta") or {}
    ref = {"slug": stage["slug"], "id": stage.get("id"), "status": stage["status"]}
    if meta.get("lead"):
        _agent(agents, roster, meta["lead"])["leads"].append(ref)
    for name in meta.get("support") or []:
        _agent(agents, roster, name)["supports"].append(ref)
    if meta.get("reviewer"):
        _agent(agents, roster, meta["reviewer"])["reviews"].append(ref)


def _apply_agent_event(agents, roster, ev: dict[str, Any]) -> None:
    name, kind, ts = ev.get("agent"), ev["event"], ev["timestamp"]
    if not name:
        return
    agent = _agent(agents, roster, name)
    agent["events"] += 1
    agent["firstSeen"] = agent["firstSeen"] or ts
    agent["lastSeen"] = ts
    if kind == ev_names.STAGE_STARTED and ev.get("stage") and ev["stage"] not in agent["ran"]:
        agent["ran"].append(ev["stage"])
    elif kind in (ev_names.SUBAGENT_DISPATCHED, ev_names.REVIEW_REQUESTED):
        agent["dispatches"] += 1
        agent["lastDispatch"] = ts
    elif kind in (ev_names.SUBAGENT_COMPLETED, ev_names.REVIEW_COMPLETED):
        agent["completions"] += 1
        agent["lastCompletion"] = ts


def _agent_state(agent: dict[str, Any], stages: list[dict[str, Any]], active_agent: str | None) -> str:
    """working: leads an active stage or has an unfinished dispatch. contributed: has audit history.
    planned: assigned to stages still ahead. no-record: assigned only to finished stages, no trace. idle: not in this run."""
    led_now = {s["slug"] for s in agent["leads"] if s["status"] in ("in-progress", "revising")}
    open_dispatch = bool(agent["lastDispatch"]) and (agent["lastCompletion"] or "") < agent["lastDispatch"]
    if led_now or open_dispatch:
        return "working"
    if any(s["status"] == "awaiting-approval" for s in agent["leads"]):
        return "awaiting-approval"
    if agent["name"] == active_agent and any(s["status"] in ACTIVE_STATUSES for s in stages):
        return "working"
    if agent["ran"] or agent["completions"] or any(s["status"] == "completed" for s in agent["leads"]):
        return "contributed"
    assigned = agent["leads"] + agent["supports"] + agent["reviews"]
    if not assigned:
        return "idle"
    # Support/review roles only show in the audit when dispatched; finished stages with no trace are "no-record".
    return "planned" if any(s["status"] not in ("completed", "skipped") for s in assigned) else "no-record"


# --------------------------------------------------------------------------- sessions


def read_sessions(root: Path) -> dict[str, Any]:
    base = root / SESSIONS_DIR
    current = base / ".current-session"
    active_space = root / "aidlc" / "active-space"
    current_id = _read_text(current).strip() if current.is_file() else None
    return {
        "activeSpace": _read_text(active_space).strip() if active_space.is_file() else None,
        "currentSession": current_id,
        "binding": _read_json(base / f"{current_id}.binding.json") if current_id else None,
        "questions": _json_dir(base / "questions", "createdAt"),
        "messages": _json_dir(base / "messages", "at")[-10:],
    }


def _json_dir(path: Path, sort_key: str) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    rows = [r for r in (_read_json(p) for p in path.glob("*.json")) if isinstance(r, dict)]
    return sorted(rows, key=lambda r: str(r.get(sort_key, "")))


# --------------------------------------------------------------------------- top-level model


def build_status(root: Path, record_id: str | None = None, stall_minutes: int = insights.DEFAULT_STALL_MINUTES,
                 harness_name: str | None = None) -> dict[str, Any]:
    harness, warnings = select_harness(root, harness_name)
    graph = load_stage_graph(harness)
    grid = load_scope_grid(harness)
    catalog = load_stage_catalog(harness, graph)
    order, order_source = canonical_order(grid, catalog, graph)
    version = aidlc_version(harness)
    warnings += [w for w in (version_warning(version), order_warning(order_source, bool(catalog))) if w]
    base = {
        "pulseVersion": PULSE_VERSION,
        "project": str(root),
        "harness": harness.name if harness else None,
        "aidlcVersion": version,
        "testedVersion": ev_names.TESTED_AIDLC_RELEASE,
        "stageOrderSource": order_source,
        "warnings": warnings,
        "records": discover_records(root),
        "sessions": read_sessions(root),
        "serverNow": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not base["records"]:
        message = "No aidlc-state.md found in this project yet."
        agents = agent_activity(load_roster(harness), [], [], None)
        return {**base, "record": None, "message": message, "phases": _empty_phases(order, catalog), "agents": agents}
    record = resolve_record(root, record_id)  # unknown id -> RecordNotFound -> 404
    status = _record_status(record, order, catalog, load_roster(harness))
    derived = _insights(status, base["sessions"], stall_minutes)  # pops the internal event lists first
    return {**base, **status, **derived}


def _insights(status: dict[str, Any], sessions: dict[str, Any], stall_minutes: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stages = [s for p in status["phases"] for s in p["stages"]]
    events = status.pop("_allEvents")
    space_events = status.pop("_spaceEvents")
    # Space-level events show the engine is active (stall detection) but aren't credited to this record's stages.
    activity = sorted(events + space_events, key=_event_sort_key) if space_events else events
    return {
        "attention": insights.attention(stages, events, sessions, now),
        "health": insights.health(stages, activity, now, stall_minutes),
        "timing": insights.timing(stages, events, now, stall_minutes),
    }


def build_audit(root: Path, record_id: str | None, harness_name: str | None = None) -> dict[str, Any]:
    """The full audit trail of one record plus its space-level shard, newest first (capped)."""
    record = resolve_record(root, record_id)
    harness = select_harness(root, harness_name)[0]
    known = _known_stages(load_stage_catalog(harness), Path(record["path"]))
    events = parse_audit(audit_files(Path(record["path"])), known_stages=known)
    space = parse_audit(space_audit_files(record), source="space", known_stages=known)
    merged = sorted(events + space, key=_event_sort_key) if space else events
    return {"record": record["id"], "total": len(merged), "spaceEvents": len(space),
            "events": merged[-MAX_AUDIT_EVENTS:][::-1]}


def _record_status(record: dict[str, Any], order: list[str], catalog: dict[str, dict[str, Any]],
                   roster: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record_dir = Path(record["path"])
    state = parse_state(_read_text(record_dir / STATE_FILE))
    known = _known_stages(catalog, record_dir, state)
    events = parse_audit(audit_files(record_dir), known_stages=known)
    space_events = parse_audit(space_audit_files(record), source="space", known_stages=known)
    timeline = stage_timeline(events)
    ids = stage_ids(order, catalog)
    rows = _with_missing_stages(state["stages"], order, catalog)
    stages = [_merge_stage(row, catalog, ids, timeline, record_dir) for row in rows]
    active_agent = state["sections"].get("Project Information", {}).get("Active Agent") or None
    return {
        "record": record,
        "sections": state["sections"],
        "phases": _group_by_phase(stages),
        "summary": _summary(stages, state["sections"]),
        "agents": agent_activity(roster, stages, events, active_agent),
        "units": units.build_units(record_dir, events, stages, state["sections"]),
        "decisions": decisions.build_decisions(record_dir, stages, _read_text),
        "events": events[-RECENT_EVENT_LIMIT:][::-1],
        "eventCount": len(events) + len(space_events),
        "spaceEventCount": len(space_events),
        "_allEvents": events,
        "_spaceEvents": space_events,
    }


def _known_stages(catalog: dict[str, dict[str, Any]], record_dir: Path, state: dict[str, Any] | None = None) -> frozenset[str]:
    """Stage slugs from the catalog and the state file: lets artifact paths name their stage exactly."""
    state = state or parse_state(_read_text(record_dir / STATE_FILE))
    return frozenset(catalog) | {s["slug"] for s in state["stages"]}


def _with_missing_stages(rows: list[dict[str, str]], order: list[str], catalog: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Add catalog stages the state file never listed (older runs) as not-in-scope, in canonical order."""
    listed = {r["slug"] for r in rows}
    extra = [{"slug": s, "stateSlug": s, "phase": catalog.get(s, {}).get("phase", ""), "mark": " ",
              "status": "pending", "decision": "SKIP"} for s in order if s not in listed]
    rank = {slug: i for i, slug in enumerate(order)}
    return sorted(rows + extra, key=lambda r: rank.get(r["slug"], len(rank)))


def _merge_stage(row, catalog, ids, timeline, record_dir) -> dict[str, Any]:
    meta = catalog.get(row["slug"], {})
    phase = row["phase"] or meta.get("phase", "")
    return {
        **row,
        "phase": phase,
        "id": ids.get(row["slug"]),
        "meta": meta,
        "timeline": timeline.get(row["slug"]) or timeline.get(row["stateSlug"]),
        "artifacts": list_artifacts(record_dir, phase, row["slug"], row["stateSlug"]),
    }


def _group_by_phase(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = {p: [] for p in PHASES}
    for stage in stages:
        groups.setdefault(stage["phase"], []).append(stage)
    return [{"name": name, "stages": rows} for name, rows in groups.items() if rows]


def _empty_phases(order: list[str], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ids = stage_ids(order, catalog)
    rows = [{"slug": s, "stateSlug": s, "phase": catalog.get(s, {}).get("phase", ""), "mark": " ", "status": "pending",
             "decision": "EXECUTE", "id": ids.get(s), "meta": catalog.get(s, {}), "timeline": None, "artifacts": []} for s in order]
    return _group_by_phase(rows)


def _summary(stages: list[dict[str, Any]], sections: dict[str, dict[str, str]]) -> dict[str, Any]:
    in_scope = [s for s in stages if s["decision"] == "EXECUTE"]
    counts: dict[str, int] = {}
    for stage in stages:
        counts[stage["status"]] = counts.get(stage["status"], 0) + 1
    done = sum(1 for s in in_scope if s["status"] in ("completed", "skipped"))
    return {
        "inScope": len(in_scope),
        "outOfScope": len(stages) - len(in_scope),
        "done": done,
        "percent": round(100 * done / len(in_scope)) if in_scope else 0,
        "counts": counts,
        "awaitingApproval": [s["slug"] for s in stages if s["status"] == "awaiting-approval"],
        "active": [s["slug"] for s in stages if s["status"] in ("in-progress", "revising")],
        "phaseProgress": sections.get("Phase Progress", {}),
    }
