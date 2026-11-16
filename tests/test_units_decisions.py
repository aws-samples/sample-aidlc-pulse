import unittest

import decisions
import reader
import units
from tests.fixtures import DAG, Project, audit_text, event


class UnitPlanTest(unittest.TestCase):
    def test_edge_block(self):
        plan = units.parse_edge_block(DAG)
        self.assertEqual([(u["name"], u["kind"], u["deps"]) for u in plan],
                         [("u1-items", "service", []), ("u2-tags", "service", ["u1-items"])])

    def test_edge_block_list_deps_and_unknown_dep_dropped(self):
        text = "```yaml\nunits:\n  - name: a\n  - name: b\n    depends_on:\n      - a\n      - ghost\n```\n"
        self.assertEqual([u["deps"] for u in units.parse_edge_block(text)], [[], ["a"]])

    def test_legacy_matrix(self):
        text = "| Unit | Depends On |\n|---|---|\n| Unit 1 (Store) | None |\n| Unit 2 (API) | 1 |\n| Unit 3 (UI) | 1-2 |\n"
        plan = units.parse_legacy_matrix(text)
        self.assertEqual([(u["label"], u["deps"]) for u in plan],
                         [("Store", []), ("API", ["unit-1"]), ("UI", ["unit-1", "unit-2"])])


class UnitProgressTest(unittest.TestCase):
    def setUp(self):
        reader.CACHE.clear()
        self.project = Project()
        self.addCleanup(self.project.cleanup)

    def units(self):
        return reader.build_status(self.project.root)["units"]

    def test_paused_unit(self):
        info = self.units()
        u1 = info["units"][0]
        self.assertEqual(u1["status"], "paused")
        self.assertEqual(u1["paused"]["reason"], "npm install failed")
        self.assertEqual(u1["stages"]["functional-design"], "completed")
        self.assertEqual(info["summary"]["pausedUnits"][0]["unit"], "u1-items")

    def test_checkpoint_approval_marks_unit_done(self):
        self.project.write("audit/later.md", audit_text([
            event("2026-11-03T10:30:00Z", "UNIT_RESUMED", "code-generation", Unit="u1-items"),
            event("2026-11-03T10:40:00Z", "UNIT_COMPLETED", "code-generation", Unit="u1-items"),
            event("2026-11-03T10:41:00Z", "GATE_APPROVED", "code-generation", Unit="u1-items", Checkpoint="construction-unit"),
        ]))
        info = self.units()
        self.assertEqual(info["doneBy"], "checkpoint")
        self.assertEqual(info["units"][0]["status"], "completed")
        self.assertEqual(info["summary"]["done"], 1)


class DecisionsTest(unittest.TestCase):
    def test_options_answers_and_mode(self):
        from tests.fixtures import QUESTIONS
        q1, q2 = decisions.parse_questions(QUESTIONS)["questions"]
        self.assertEqual((q1["id"], q1["role"], q1["text"]), ("Q1", "Product manager", "What does an item hold?"))
        self.assertEqual([(o["key"], o["recommended"]) for o in q1["options"]], [("A", True), ("B", False), ("X", False)])
        self.assertEqual(q1["options"][0]["text"], "A name and a URL")
        self.assertEqual((q1["chosen"], q1["mode"], q1["answered"]), ("A", "chat", True))
        self.assertEqual(q1["answer"], "A (chosen as recommended)")
        self.assertFalse(q2["answered"])
        self.assertIsNone(q2["chosen"])

    def test_bulleted_plan_approval_with_metadata(self):
        text = ("# Code Generation Plan Approval\n\nWrite your answer after `[Answer]:`.\n\n## Plan Approval\n\n"
                "Approve the code plan?\n\n- Builds: the store\n\n[Approval Fingerprint]: sha256:abc\n\n"
                "- A. Approve Plan\n- B. Request Changes\n\n[Answer]: A. Approve Plan\n")
        (q,) = decisions.parse_questions(text)["questions"]
        self.assertEqual(q["text"], "Plan Approval")
        self.assertEqual([o["key"] for o in q["options"]], ["A", "B"])
        self.assertEqual(q["chosen"], "A")
        self.assertNotIn("sha256", q["context"])

    def test_free_text_and_colon_ids(self):
        text = "# Deployment Questions\n\n[Answer]: Blue/green with a health check.\n"
        (q,) = decisions.parse_questions(text)["questions"]
        self.assertEqual((q["text"], q["answer"], q["chosen"]), ("Deployment Questions", "Blue/green with a health check.", None))
        (q,) = decisions.parse_questions("# T\n\n## Q2: Strategy?\n\n[Answer]: Autonomous, no gates\n")["questions"]
        self.assertEqual((q["id"], q["text"], q["chosen"]), ("Q2", "Strategy?", None))  # "Autonomous" is not option A

    def test_record_files_ordered_and_unit_attributed(self):
        reader.CACHE.clear()
        with Project() as p:
            p.write("construction/u1-items/code-generation/code-generation-questions.md",
                    "# Plan\n\n## Plan Approval\n\n- A. Approve Plan\n\n[Answer]:\n")
            dc = reader.build_status(p.root)["decisions"]
        self.assertEqual([(f["stage"], f["unit"]) for f in dc["files"]],
                         [("requirements-analysis", None), ("code-generation", "u1-items")])
        self.assertEqual((dc["questionCount"], dc["openCount"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
