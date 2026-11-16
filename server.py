#!/usr/bin/env python3
"""Start AI-DLC Pulse, a local, read-only dashboard for an AI-DLC v2 project.

    python3 server.py --project /path/to/aidlc-project   # as in 1.0.0
    python3 server.py /path/to/aidlc-project             # same, without the flag
    python3 server.py                                     # asks for the project folder

Every option of src/http_server.py passes through (see --help). Binds to 127.0.0.1 only and never writes to
the project.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import http_server  # noqa: E402
import reader  # noqa: E402

PROMPT = "AI-DLC project folder"
RECORD_DIRS = (Path("aidlc"), Path(reader.LEGACY_RECORD_DIR))
PROJECT_FLAGS = ("--project", "-p")
HELP_FLAGS = ("-h", "--help")
YES = ("y", "yes")


def clean_path(raw: str) -> str:
    """Accept what people paste or drag into a terminal: quotes, `\\ ` escaped spaces, a leading `~`."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text.replace("\\ ", " ")


def looks_like_project(path: Path) -> bool:
    """A folder with AI-DLC records (aidlc/ or legacy aidlc-docs/) or an installed harness."""
    return any((path / d).is_dir() for d in RECORD_DIRS) or bool(reader.find_harnesses(path))


def ask_project(ask: Callable[[str], str] = input, default: Path | None = None) -> Path:
    """Ask until the answer is a folder; confirm folders that don't look like AI-DLC projects."""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = clean_path(ask(f"{PROMPT}{suffix}: ")) or (str(default) if default else "")
        if not answer:
            continue
        path = Path(answer).expanduser().resolve()
        if not path.is_dir():
            print(f"  Not a folder: {path}")
            continue
        if looks_like_project(path) or ask("  No aidlc/ or harness folder there. Use it anyway? [y/N]: ").strip().lower() in YES:
            return path


def split_args(argv: list[str]) -> tuple[str | None, list[str]]:
    """(project folder from argv or None, remaining options). Takes `--project X`, `--project=X` or a first bare path."""
    rest = list(argv)
    for i, arg in enumerate(rest):
        if arg in PROJECT_FLAGS and i + 1 < len(rest):
            return rest[i + 1], rest[:i] + rest[i + 2:]
        if arg.startswith("--project="):
            return arg.split("=", 1)[1], rest[:i] + rest[i + 1:]
    if rest and not rest[0].startswith("-"):
        return rest[0], rest[1:]
    return None, rest


def main(argv: list[str] | None = None, ask: Callable[[str], str] = input) -> None:
    args = sys.argv[1:] if argv is None else argv
    if any(a in HELP_FLAGS for a in args):
        http_server.main(list(args))
        return
    folder, options = split_args(args)
    if folder is not None:
        project = Path(clean_path(folder)).expanduser().resolve()
        if not project.is_dir():
            raise SystemExit(f"Not a folder: {project}")
    elif sys.stdin.isatty() or ask is not input:
        cwd = Path.cwd()
        project = ask_project(ask, default=cwd if looks_like_project(cwd) else None)
    else:
        raise SystemExit("No project folder given. Run: python3 server.py --project <folder>")
    print(f"Starting AI-DLC Pulse for {project}. Press Ctrl+C to stop.")
    http_server.main(["--project", str(project), *options])


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
