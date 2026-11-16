"""Decision record: the questions each stage asked, the options offered, and the answer given.

Source: every `<slug>-questions.md` in a record, at `<phase>/<slug>/` or, for per-unit Construction stages,
`construction/<unit>/<slug>/`. AI-DLC writes them in a few shapes, all handled here:

    ## Q1. [Architect] Where does the server wiring live?      (role tag optional; "## Q1: ..." also seen)
    A. A separate composition root (Recommended)
    B. Inside the bookmarks module
    X. Other (please specify)
    [Answer]: A (chosen as recommended) **Mode:** chat

plus "## Plan Approval" sections and files that hold only a single `[Answer]:` line under the title.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

QUESTIONS_SUFFIX = "-questions.md"
MAX_QUESTION_FILES = 500

_HEADING = re.compile(r"^(#{2,4})\s+(.*?)\s*$")
_TITLE = re.compile(r"^#\s+(.*?)\s*$")
_QID = re.compile(r"^(Q\d+)\s*[.:)]\s*", re.I)
_ROLE = re.compile(r"^\[([^\]]+)\]\s*")
_OPTION = re.compile(r"^(?:[-*]\s+)?([A-Z])[.)]\s+(.*)$")
_META_LINE = re.compile(r"^\[[^\]]+\]:")  # e.g. [Approval Fingerprint]: sha256:...
MAX_CONTEXT_CHARS = 600
_ANSWER = re.compile(r"^\[Answer\]:\s*(.*)$", re.I)
_MODE = re.compile(r"\s*\*\*Mode:\*\*\s*(\S+)\s*")
_RECOMMENDED = re.compile(r"\s*\(Recommended\)\s*$", re.I)
_ANSWER_KEY = re.compile(r"^([A-Z])(?:\b|[.)])")


def build_decisions(record_dir: Path, stages: list[dict[str, Any]], read: Callable[[Path], str]) -> dict[str, Any]:
    """{files: [...], openCount, questionCount}. Files follow stage order, then unit name."""
    rank = {s["slug"]: i for i, s in enumerate(stages)}
    status = {s["slug"]: s["status"] for s in stages}
    files = []
    for path in _question_files(record_dir):
        rel = path.relative_to(record_dir)
        stage, unit = _stage_and_unit(rel)
        parsed = parse_questions(read(path))
        open_ = sum(1 for q in parsed["questions"] if not q["answered"])
        files.append({"path": rel.as_posix(), "stage": stage, "unit": unit, "stageStatus": status.get(stage),
                      "title": parsed["title"], "questions": parsed["questions"], "open": open_})
    files.sort(key=lambda f: (rank.get(f["stage"], len(rank)), f["unit"] or "", f["path"]))
    return {
        "files": files,
        "questionCount": sum(len(f["questions"]) for f in files),
        "openCount": sum(f["open"] for f in files),
    }


def _question_files(record_dir: Path) -> list[Path]:
    found = []
    for path in sorted(record_dir.rglob("*" + QUESTIONS_SUFFIX)):
        rel = path.relative_to(record_dir)
        if path.is_file() and not any(part.startswith(".") for part in rel.parts):
            found.append(path)
        if len(found) >= MAX_QUESTION_FILES:
            break
    return found


def _stage_and_unit(rel: Path) -> tuple[str, str | None]:
    """`inception/<slug>/x-questions.md` -> (slug, None); `construction/<unit>/<slug>/...` -> (slug, unit)."""
    slug = rel.name[: -len(QUESTIONS_SUFFIX)]
    parts = rel.parts[:-1]
    unit = parts[1] if len(parts) >= 3 and parts[0] == "construction" and parts[2] == slug else None
    return slug, unit


def parse_questions(text: str) -> dict[str, Any]:
    """Split a questions file into question sections. A section counts if it has options or an [Answer] line."""
    title, sections, current = None, [], {"heading": None, "lines": []}
    for line in text.splitlines():
        if title is None and (m := _TITLE.match(line)):
            title = m.group(1)
        elif m := _HEADING.match(line):
            sections.append(current)
            current = {"heading": m.group(2), "lines": []}
        else:
            current["lines"].append(line)
    sections.append(current)
    questions = [q for q in (_question(s, title) for s in sections) if q]
    return {"title": title, "questions": questions}


def _question(section: dict[str, Any], title: str | None) -> dict[str, Any] | None:
    options, context, answer_lines, in_answer = [], [], None, False
    for raw in section["lines"]:
        line = raw.strip()
        if m := _ANSWER.match(line):
            answer_lines, in_answer = [m.group(1)], True
        elif in_answer:
            answer_lines.append(line)
        elif m := _OPTION.match(line):
            text = m.group(2).strip()
            options.append({"key": m.group(1), "text": _RECOMMENDED.sub("", text),
                            "recommended": bool(_RECOMMENDED.search(text))})
        elif line and not _META_LINE.match(line):
            context.append(line)
    if answer_lines is None and not options:
        return None
    qid, role, text = _heading_parts(section["heading"] or title or "")
    answer = _answer(" ".join(a for a in (answer_lines or []) if a).strip(), options)
    joined = " ".join(context)
    return {"id": qid, "role": role, "text": text,
            "context": (joined[:MAX_CONTEXT_CHARS] + "…" if len(joined) > MAX_CONTEXT_CHARS else joined) or None,
            "options": options, **answer}


def _heading_parts(heading: str) -> tuple[str | None, str | None, str]:
    qid = role = None
    if m := _QID.match(heading):
        qid, heading = m.group(1).upper(), heading[m.end():]
    if m := _ROLE.match(heading):
        role, heading = m.group(1).strip(), heading[m.end():]
    return qid, role, heading.strip()


def _answer(raw: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    mode = None
    if m := _MODE.search(raw):
        mode, raw = m.group(1), (raw[: m.start()] + " " + raw[m.end():]).strip()
    keys = {o["key"] for o in options}
    chosen = m.group(1) if (m := _ANSWER_KEY.match(raw)) and m.group(1) in keys else None
    return {"answer": raw or None, "chosen": chosen, "mode": mode, "answered": bool(raw)}
