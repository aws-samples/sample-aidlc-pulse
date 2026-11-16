import os
import unittest

import reader
from tests.fixtures import RECORD_ID, Project, audit_text, event


class ReaderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reader.CACHE.clear()
        self.project = Project()
        self.addCleanup(self.project.cleanup)

    def status(self, **kw):
        return reader.build_status(self.project.root, **kw)

    def stages(self, status):
        return {s["slug"]: s for p in status["phases"] for s in p["stages"]}


class StateAndStagesTest(ReaderTestCase):
    def test_state_file_statuses(self):
        st = self.stages(self.status())
        self.assertEqual(st["requirements-analysis"]["status"], "completed")
        self.assertEqual(st["functional-design"]["status"], "in-progress")
        self.assertEqual(st["build-and-test"]["decision"], "SKIP")

    def test_stage_graph_gives_order_numbers_and_names(self):
        d = self.status()
        self.assertEqual(d["stageOrderSource"], "stage-graph")
        st = self.stages(d)
        self.assertEqual(st["code-generation"]["id"], "3.5")  # the graph's own number, not a count (would be 3.2)
        self.assertEqual(st["code-generation"]["meta"]["name"], "Code Generation")
        self.assertEqual(st["functional-design"]["meta"]["forEach"], "unit-of-work")
        self.assertEqual(d["warnings"], [])

    def test_scope_grid_fallback_without_stage_graph(self):
        (self.project.harness / reader.STAGE_GRAPH).unlink()
        d = self.status()
        self.assertEqual(d["stageOrderSource"], "scope-grid")
        self.assertEqual(self.stages(d)["code-generation"]["id"], "3.2")
        self.assertEqual(d["warnings"], [])

    def test_guessed_order_is_reported(self):
        (self.project.harness / reader.STAGE_GRAPH).unlink()
        (self.project.harness / reader.SCOPE_GRID).unlink()
        d = self.status()
        self.assertEqual(d["stageOrderSource"], "guessed")
        self.assertTrue(any("Stage order is a guess" in w for w in d["warnings"]))

    def test_revisions_count_change_requests_once(self):
        timeline = self.stages(self.status())["requirements-analysis"]["timeline"]
        self.assertEqual((timeline["gates"], timeline["approvals"], timeline["revisions"]), (2, 1, 1))


class HarnessAndVersionTest(ReaderTestCase):
    def test_version_from_stamp(self):
        d = self.status()
        self.assertEqual(d["aidlcVersion"], "2.11.2")

    def test_untested_version_warns(self):
        (self.project.harness / reader.STAMP_FILE).write_text('{"frameworkVersion": "3.0.1"}')
        self.assertTrue(any("AI-DLC 3.0.1" in w for w in self.status()["warnings"]))

    def test_several_harnesses_warn_and_can_be_chosen(self):
        self.project.add_harness(".claude")
        d = self.status()
        self.assertEqual(d["harness"], ".kiro")
        self.assertTrue(any("Several AI-DLC harnesses" in w for w in d["warnings"]))
        d = self.status(harness_name=".claude")
        self.assertEqual((d["harness"], d["warnings"]), (".claude", []))

    def test_missing_preferred_harness_warns(self):
        d = self.status(harness_name=".codex")
        self.assertEqual(d["harness"], ".kiro")
        self.assertTrue(any(".codex not found" in w for w in d["warnings"]))


class AuditTest(ReaderTestCase):
    def test_sorted_by_time_not_text(self):
        events = [event("2026-11-03T11:30:00+02:00", "STAGE_STARTED", "a"),  # 09:30Z
                  event("2026-11-03T10:00:00Z", "STAGE_STARTED", "b"),
                  event("2026-11-03T09:45:00.500Z", "STAGE_STARTED", "c")]
        path = self.project.write("audit/x.md", audit_text(events))
        self.assertEqual([e["stage"] for e in reader.parse_audit([path])], ["a", "c", "b"])

    def test_stage_inference_skips_run_level_events(self):
        events = {e["event"]: e for e in reader.parse_audit(reader.audit_files(self.project.record))}
        self.assertEqual(events["ARTIFACT_CREATED"]["stage"], "functional-design")
        self.assertTrue(events["ARTIFACT_CREATED"]["stageInferred"])
        self.assertIsNone(events["WORKFLOW_STARTED"]["stage"])

    def test_space_level_shard_shown_but_not_credited(self):
        shard = self.project.record.parent / "audit" / "host-abc123.md"
        shard.parent.mkdir()
        shard.write_text(audit_text([event("2026-11-03T10:30:00Z", "STAGE_STARTED", "code-generation")]))
        audit = reader.build_audit(self.project.root, RECORD_ID)
        self.assertEqual(audit["spaceEvents"], 1)
        self.assertEqual(audit["events"][0]["source"], "space")  # newest first
        d = self.status()
        self.assertEqual(d["health"]["lastEventAt"], "2026-11-03T10:30:00Z")
        self.assertIsNone(self.stages(d)["code-generation"]["timeline"]["startedAt"])

    def test_cache_rereads_changed_files(self):
        path = self.project.write("audit/y.md", audit_text([event("2026-11-03T10:00:00Z", "STAGE_STARTED", "a")]))
        self.assertEqual(len(reader.parse_audit([path])), 1)
        path.write_text(audit_text([event("2026-11-03T10:00:00Z", "STAGE_STARTED", "a"),
                                    event("2026-11-03T10:01:00Z", "STAGE_COMPLETED", "a")]))
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        self.assertEqual(len(reader.parse_audit([path])), 2)

    def test_cached_events_are_not_mutated_by_inference(self):
        path = self.project.write("audit/z.md", audit_text([event("2026-11-03T10:00:00Z", "STAGE_STARTED", "a"),
                                                             event("2026-11-03T10:01:00Z", "ARTIFACT_CREATED")]))
        reader.parse_audit([path])
        again = reader.parse_audit([self.project.write("audit/z2.md", "")] + [path])
        self.assertEqual(again[1]["stage"], "a")
        cached = reader.CACHE.get(path, "audit", reader._parse_audit_file)
        self.assertIsNone(cached[1]["stage"])


class ArtifactTest(ReaderTestCase):
    def read(self, rel):
        return reader.read_artifact(self.project.root, RECORD_ID, rel)

    def test_unit_artifacts_listed_with_their_stage(self):
        arts = self.stages(self.status())["functional-design"]["artifacts"]
        self.assertIn(("construction/u1-items/functional-design/business-logic.md", "u1-items"),
                      [(a["path"], a.get("unit")) for a in arts])

    def test_spaces_and_non_ascii_names_open(self):
        for name in ("notes with spaces.md", "décisions-équipe.md", "设计.md"):
            self.project.write(f"inception/requirements-analysis/{name}", "ok")
            self.assertEqual(self.read(f"inception/requirements-analysis/{name}")["text"], "ok")

    def test_refuses_escapes_and_odd_segments(self):
        (self.project.root / "secret.md").write_text("no")
        self.project.write(".aidlc-engine/x.md", "hidden")
        for bad in ("../../../../secret.md", "inception/../../secret.md", "/etc/hosts", ".aidlc-engine/x.md",
                    "inception\\..\\x.md", "a\x00b.md", "inception/ x.md", "", "inception//x.md"):
            with self.subTest(path=bad), self.assertRaises(reader.ArtifactRefused):
                self.read(bad)

    def test_refuses_symlink_out_of_record(self):
        outside = self.project.root / "outside.md"
        outside.write_text("no")
        (self.project.record / "inception" / "link.md").symlink_to(outside)
        with self.assertRaises(reader.ArtifactRefused):
            self.read("inception/link.md")

    def test_refuses_unviewable_types(self):
        self.project.write("inception/tool.sh", "echo")
        with self.assertRaises(reader.ArtifactRefused):
            self.read("inception/tool.sh")

    def test_unknown_record(self):
        with self.assertRaises(reader.RecordNotFound):
            reader.read_artifact(self.project.root, "nope/nope", "aidlc-state.md")


if __name__ == "__main__":
    unittest.main()


class StageAttributionTest(ReaderTestCase):
    """Events with no Stage field, including while units run in parallel."""

    KNOWN = frozenset({"functional-design", "code-generation", "requirements-analysis"})
    REC = "<project-dir>/aidlc/spaces/default/intents/261103-sample-api"

    def parse(self, events):
        return reader.parse_audit([self.project.write("audit/attr.md", audit_text(events))], known_stages=self.KNOWN)

    def parallel_start(self):
        return [event("2026-11-03T10:00:00Z", "STAGE_STARTED", "functional-design"),
                event("2026-11-03T10:01:00Z", "UNIT_STARTED", "functional-design", Unit="u1-items"),
                event("2026-11-03T10:02:00Z", "UNIT_STARTED", "code-generation", Unit="u2-tags")]

    def test_parallel_units_leave_unscoped_events_unattributed(self):
        evs = self.parse(self.parallel_start() + [event("2026-11-03T10:03:00Z", "SUBAGENT_DISPATCHED", **{"Agent Type": "x"})])
        last = evs[-1]
        self.assertIsNone(last["stage"])
        self.assertEqual(last["stageAmbiguous"], ["code-generation", "functional-design"])

    def test_unit_field_names_the_stage(self):
        evs = self.parse(self.parallel_start() + [event("2026-11-03T10:03:00Z", "REVIEW_REQUESTED", Unit="u2-tags")])
        self.assertEqual((evs[-1]["stage"], evs[-1].get("stageInferred")), ("code-generation", True))

    def test_file_path_names_stage_and_unit(self):
        evs = self.parse(self.parallel_start() + [
            event("2026-11-03T10:03:00Z", "ARTIFACT_CREATED", File=f"{self.REC}/construction/u2-tags/code-generation/plan.md"),
            event("2026-11-03T10:04:00Z", "ARTIFACT_UPDATED", File=f"{self.REC}/inception/requirements-analysis/memory.md")])
        self.assertEqual([(e["stage"], e.get("stageFrom"), e.get("unit")) for e in evs[-2:]],
                         [("code-generation", "path", "u2-tags"), ("requirements-analysis", "path", None)])
        self.assertFalse(any(e.get("stageInferred") for e in evs[-2:]))

    def test_stage_slug_field(self):
        evs = self.parse(self.parallel_start() + [event("2026-11-03T10:03:00Z", "SENSOR_FIRED", **{"Stage slug": "code-generation"})])
        self.assertEqual((evs[-1]["stage"], evs[-1]["stageFrom"]), ("code-generation", "field"))

    def test_single_active_unit_and_fallback_after_pause(self):
        evs = self.parse([
            event("2026-11-03T10:00:00Z", "STAGE_STARTED", "functional-design"),
            event("2026-11-03T10:01:00Z", "UNIT_STARTED", "code-generation", Unit="u1-items"),
            event("2026-11-03T10:02:00Z", "HUMAN_TURN"),
            event("2026-11-03T10:03:00Z", "UNIT_PAUSED", "code-generation", Unit="u1-items"),
            event("2026-11-03T10:04:00Z", "HUMAN_TURN")])
        self.assertEqual([evs[2]["stage"], evs[4]["stage"]], ["code-generation", "functional-design"])

    def test_paths_outside_a_record_are_ignored(self):
        evs = self.parse([event("2026-11-03T10:00:00Z", "STAGE_STARTED", "functional-design"),
                          event("2026-11-03T10:01:00Z", "ARTIFACT_CREATED", File="<project-dir>/src/app.ts")])
        self.assertEqual((evs[-1]["stage"], evs[-1].get("stageInferred")), ("functional-design", True))
