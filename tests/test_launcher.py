"""The server.py launcher: path cleaning, project detection, prompting and argument hand-off."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server as run
from tests.fixtures import Project


def answers(*replies: str):
    it = iter(replies)
    return lambda _prompt: next(it)


class CleanPathTest(unittest.TestCase):
    def test_pasted_and_dragged_forms(self):
        self.assertEqual(run.clean_path('  "/a b/c"  '), "/a b/c")
        self.assertEqual(run.clean_path("'/a b'"), "/a b")
        self.assertEqual(run.clean_path("/Users/me/My\\ Project "), "/Users/me/My Project")
        self.assertEqual(run.clean_path(""), "")


class AskProjectTest(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.addCleanup(self.project.cleanup)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)

    def test_reasks_until_a_project_folder(self):
        got = run.ask_project(answers("", "/no/such/folder", str(self.project.root)))
        self.assertEqual(got, self.project.root)

    def test_default_used_on_empty_answer(self):
        self.assertEqual(run.ask_project(answers(""), default=self.project.root), self.project.root)

    def test_plain_folder_needs_confirmation(self):
        with tempfile.TemporaryDirectory() as plain:
            plain_path = Path(plain).resolve()
            got = run.ask_project(answers(plain, "n", plain, "y"))
        self.assertEqual(got, plain_path)

    def test_detection(self):
        self.assertTrue(run.looks_like_project(self.project.root))
        with tempfile.TemporaryDirectory() as plain:
            self.assertFalse(run.looks_like_project(Path(plain)))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.project = Project()
        self.addCleanup(self.project.cleanup)
        patcher = mock.patch.object(run.http_server, "main")
        self.server_main = patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, argv, ask=None):
        with contextlib.redirect_stdout(io.StringIO()):
            run.main(argv, ask=ask or answers())
        return self.server_main.call_args.args[0]

    def test_positional_folder_and_options_pass_through(self):
        args = self.run_main([str(self.project.root), "--port", "9000"])
        self.assertEqual(args, ["--project", str(self.project.root), "--port", "9000"])

    def test_project_flag_passes_through(self):
        args = self.run_main(["--port", "9000", "--project", str(self.project.root)])
        self.assertEqual(args, ["--project", str(self.project.root), "--port", "9000"])

    def test_asks_when_no_folder_given(self):
        args = self.run_main(["--stall-minutes", "5"], ask=answers(str(self.project.root)))
        self.assertEqual(args, ["--project", str(self.project.root), "--stall-minutes", "5"])

    def test_v1_command_unchanged(self):
        args = self.run_main(["--project", str(self.project.root), "--stall-minutes", "30"])
        self.assertEqual(args, ["--project", str(self.project.root), "--stall-minutes", "30"])

    def test_project_equals_form(self):
        args = self.run_main([f"--project={self.project.root}"])
        self.assertEqual(args, ["--project", str(self.project.root)])

    def test_help_passes_straight_through_without_asking(self):
        def no_question(_prompt):
            raise AssertionError("asked for a folder")
        self.run_main(["--help"], ask=no_question)
        self.assertEqual(self.server_main.call_args.args[0], ["--help"])

    def test_missing_folder_argument_is_refused_before_starting(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(["/no/such/folder"])
        self.assertIn("Not a folder", str(caught.exception))
        self.server_main.assert_not_called()

    def test_host_option_still_validated_by_server(self):
        self.server_main.side_effect = run.http_server.parse_args  # the real check runs before any socket is opened
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stderr(io.StringIO()):
            self.run_main([str(self.project.root), "--host", "0.0.0.0"])  # nosec B104 - asserts the bind is refused
        self.assertIn("loopback", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
