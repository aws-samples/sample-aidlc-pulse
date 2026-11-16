import unittest
from datetime import datetime, timezone

import insights

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def ev(ts, name, stage, unit=None):
    return {"timestamp": ts, "event": name, "stage": stage, "fields": {"Unit": unit} if unit else {}}


def stage(slug, status, decision="EXECUTE"):
    return {"slug": slug, "phase": "construction", "status": status, "decision": decision}


class TimingTest(unittest.TestCase):
    def row(self, stages, events, now=NOW):
        return insights.timing(stages, events, now)["stages"][0]

    def test_stalled_agent_segment_stops_at_last_event(self):
        r = self.row([stage("a", "in-progress")], [ev("2026-11-06T10:00:00Z", "STAGE_STARTED", "a"),
                                                   ev("2026-11-06T10:30:00Z", "ARTIFACT_CREATED", "a")])
        self.assertEqual(r["agent"], 30 * 60)
        self.assertTrue(r["segments"][-1]["idle"])

    def test_waiting_on_person_keeps_running(self):
        r = self.row([stage("a", "awaiting-approval")], [ev("2026-11-06T10:00:00Z", "STAGE_STARTED", "a"),
                                                         ev("2026-11-06T10:10:00Z", "STAGE_AWAITING_APPROVAL", "a")])
        self.assertEqual(r["human"], 110 * 60)
        self.assertTrue(r["segments"][-1]["open"])

    def test_active_run_stays_open(self):
        r = self.row([stage("a", "in-progress")], [ev("2026-11-06T11:55:00Z", "STAGE_STARTED", "a")])
        self.assertEqual(r["agent"], 5 * 60)
        self.assertTrue(r["segments"][-1]["open"])

    def test_rejected_unit_counts_as_rework(self):
        r = self.row([stage("a", "in-progress")], [
            ev("2026-11-06T11:50:00Z", "UNIT_STARTED", "a", "u1"), ev("2026-11-06T11:52:00Z", "GATE_REJECTED", "a", "u1"),
            ev("2026-11-06T11:54:00Z", "UNIT_STARTED", "a", "u1"), ev("2026-11-06T11:58:00Z", "UNIT_COMPLETED", "a", "u1")])
        self.assertEqual((r["agent"], r["rework"]), (120, 360))

    def test_unit_work_closes_stage_entry_segment(self):
        r = self.row([stage("a", "completed")], [
            ev("2026-11-06T11:00:00Z", "STAGE_STARTED", "a"), ev("2026-11-06T11:02:00Z", "UNIT_STARTED", "a", "u1"),
            ev("2026-11-06T11:05:00Z", "UNIT_PAUSED", "a", "u1")])
        self.assertEqual(r["agent"], 5 * 60)
        self.assertEqual([s.get("unit") for s in r["segments"]], [None, "u1"])

    def test_inferred_events_are_ignored(self):
        e = ev("2026-11-06T11:00:00Z", "STAGE_STARTED", "a")
        e["stageInferred"] = True
        self.assertEqual(insights.timing([stage("a", "in-progress")], [e], NOW)["stages"], [])

    def test_eta_needs_two_finished_stages(self):
        stages = [stage("a", "completed"), stage("b", "in-progress")]
        events = [ev("2026-11-06T11:00:00Z", "STAGE_STARTED", "a"), ev("2026-11-06T11:10:00Z", "STAGE_COMPLETED", "a"),
                  ev("2026-11-06T11:58:00Z", "STAGE_STARTED", "b")]
        self.assertIsNone(insights.timing(stages, events, NOW)["eta"])


class AttentionAndHealthTest(unittest.TestCase):
    def test_open_gate_with_questions_file(self):
        s = {**stage("a", "awaiting-approval"), "artifacts": [{"name": "a-questions.md", "path": "x/a-questions.md"}]}
        a = insights.attention([s], [ev("2026-11-06T11:00:00Z", "STAGE_AWAITING_APPROVAL", "a")], {}, NOW)
        self.assertEqual(a["count"], 1)
        self.assertEqual(a["gates"][0]["questionFiles"], ["x/a-questions.md"])
        self.assertEqual(a["gates"][0]["waitSeconds"], 3600)

    def test_stale_engine_questions_are_dropped(self):
        events = [ev("2026-11-06T11:00:00Z", "WORKFLOW_STARTED", None)]
        sessions = {"questions": [{"text": "old?", "createdAt": "2026-11-01T00:00:00Z"},
                                  {"text": "new?", "createdAt": "2026-11-06T11:30:00Z"}, {"text": " "}]}
        a = insights.attention([stage("a", "in-progress")], events, sessions, NOW)
        self.assertEqual([q["text"] for q in a["questions"]], ["new?"])

    def test_stall_flag(self):
        events = [ev("2026-11-06T11:00:00Z", "STAGE_STARTED", "a"),
                  {**ev("2026-11-06T11:01:00Z", "ERROR_LOGGED", "a"), "fields": {"Tool": "bash", "Error": "boom"}}]
        hl = insights.health([stage("a", "in-progress")], events, NOW, 15)
        self.assertTrue(hl["stalled"])
        self.assertEqual((hl["errorCount"], hl["activeStageErrors"]), (1, 1))
        self.assertFalse(insights.health([stage("a", "in-progress")], events, NOW, 120)["stalled"])


if __name__ == "__main__":
    unittest.main()
