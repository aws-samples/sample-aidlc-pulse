"""Event-name drift checks.

1. Every audit event name Pulse's code matches must be declared in events.KNOWN_EVENTS.
2. KNOWN_EVENTS and RUN_LEVEL_PREFIXES must exist in the engine's VALID_EVENT_TYPES. Point AIDLC_AUDIT_TS at an
   aidlc-audit.ts (upstream core/tools/aidlc-audit.ts, or <harness>/tools/aidlc-audit.ts in a project), or set
   AIDLC_SOURCE to an aidlc-workflows checkout. CI does this against the release in events.TESTED_AIDLC_RELEASE.
   Set AIDLC_REQUIRE_UPSTREAM=1 to fail instead of skip when neither is set.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

import events

ROOT = Path(__file__).resolve().parent.parent
# GitHub Actions in the public repo, GitLab CI in the maintainers' copy. Whichever are present must agree.
CI_CONFIGS = (".github/workflows/tests.yml", ".gitlab-ci.yml")
SOURCES = ["src/reader.py", "src/insights.py", "src/units.py", "src/decisions.py", "src/static/app.js"]
EVENT_LITERAL = re.compile(r"[\"']([A-Z]+(?:_[A-Z]+)+)[\"']")
NOT_EVENTS = {"TESTED_AIDLC_VERSIONS", "TESTED_AIDLC_RELEASE"}
VALID_SET = re.compile(r"const VALID_EVENT_TYPES = new Set\(\[(.*?)\]\);", re.S)


def engine_audit_ts() -> Path | None:
    if os.environ.get("AIDLC_AUDIT_TS"):
        return Path(os.environ["AIDLC_AUDIT_TS"])
    if os.environ.get("AIDLC_SOURCE"):
        return Path(os.environ["AIDLC_SOURCE"]) / "core" / "tools" / "aidlc-audit.ts"
    return None


def engine_events(path: Path) -> set[str]:
    match = VALID_SET.search(path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"VALID_EVENT_TYPES not found in {path}; the engine layout changed")
    body = re.sub(r"//[^\n]*", "", match.group(1))  # drop comments, which quote event names in prose
    return set(re.findall(r'"([A-Z0-9_]+)"', body))


class SourceUsesDeclaredEventsTest(unittest.TestCase):
    def test_ci_pins_the_tested_release(self):
        configs = [p for p in CI_CONFIGS if (ROOT / p).is_file()]
        self.assertTrue(configs, f"no CI config found; expected one of {CI_CONFIGS}")
        for path in configs:
            with self.subTest(ci=path):
                self.assertIn(f'AIDLC_RELEASE: "v{events.TESTED_AIDLC_RELEASE}"', (ROOT / path).read_text(encoding="utf-8"))
        self.assertIn(events.TESTED_AIDLC_RELEASE.rsplit(".", 1)[0], events.TESTED_AIDLC_VERSIONS)
    def test_every_matched_event_is_declared(self):
        for name in SOURCES:
            used = set(EVENT_LITERAL.findall((ROOT / name).read_text(encoding="utf-8"))) - NOT_EVENTS
            with self.subTest(file=name):
                self.assertEqual(used - events.KNOWN_EVENTS, set(), "declare these in events.KNOWN_EVENTS")


class UpstreamEventsTest(unittest.TestCase):
    def setUp(self):
        path = engine_audit_ts()
        if path is None:
            if os.environ.get("AIDLC_REQUIRE_UPSTREAM") == "1":
                self.fail("AIDLC_REQUIRE_UPSTREAM=1 but neither AIDLC_AUDIT_TS nor AIDLC_SOURCE is set")
            self.skipTest("set AIDLC_AUDIT_TS or AIDLC_SOURCE to check against the engine")
        self.engine = engine_events(path)

    def test_known_events_exist_upstream(self):
        self.assertEqual(events.KNOWN_EVENTS - self.engine, set(), "renamed or removed upstream")

    def test_run_level_prefixes_match_engine_families(self):
        for prefix in events.RUN_LEVEL_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertTrue(any(e.startswith(prefix) for e in self.engine), f"no engine event starts with {prefix}")


if __name__ == "__main__":
    unittest.main()
