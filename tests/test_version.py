"""version.py, CHANGELOG.md and what the server reports must agree."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import reader
import http_server as server
from tests.fixtures import Project
from version import PULSE_VERSION

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEAD = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", re.M)


class VersionTest(unittest.TestCase):
    def test_semver(self):
        self.assertRegex(PULSE_VERSION, SEMVER)

    def test_changelog_top_entry_is_current_version(self):
        releases = RELEASE_HEAD.findall((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertTrue(releases, "CHANGELOG.md has no '## [x.y.z] - YYYY-MM-DD' entries")
        self.assertEqual(releases[0][0], PULSE_VERSION, "add a CHANGELOG.md entry for the new version")

    def test_changelog_versions_descend_and_start_at_1_0_0(self):
        versions = [tuple(map(int, v.split("."))) for v, _ in
                    RELEASE_HEAD.findall((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))]
        self.assertEqual(versions, sorted(versions, reverse=True))
        self.assertEqual(versions[-1], (1, 0, 0))

    def test_reported_by_server_and_status(self):
        self.assertEqual(server.StatusHandler.server_version, f"aidlc-pulse/{PULSE_VERSION}")
        reader.CACHE.clear()
        with Project() as p:
            self.assertEqual(reader.build_status(p.root)["pulseVersion"], PULSE_VERSION)


if __name__ == "__main__":
    unittest.main()
