import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import reader
import http_server as server
from tests.fixtures import RECORD_ID, Project

ROOT = Path(__file__).resolve().parent.parent


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reader.CACHE.clear()
        cls.project = Project()
        server.StatusHandler.project = cls.project.root
        server.StatusHandler.allowed_hosts = frozenset({"127.0.0.1", "localhost", "::1"})
        server.StatusHandler.log_message = lambda *a, **k: None  # keep test output clean
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.StatusHandler)  # loopback, ephemeral port
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.project.cleanup()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def test_status_ok_with_security_headers(self):
        resp, body = self.get("/api/status")
        self.assertEqual(resp.status, 200)
        self.assertIn("script-src 'self'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        data = json.loads(body)
        self.assertEqual(data["record"]["id"], RECORD_ID)
        self.assertIn("decisions", data)

    def test_foreign_host_header_refused(self):
        resp, _ = self.get("/api/status", host="evil.example:80")
        self.assertEqual(resp.status, 403)

    def test_unknown_route_and_record(self):
        self.assertEqual(self.get("/etc/passwd")[0].status, 404)
        self.assertEqual(self.get("/api/status?record=nope")[0].status, 404)

    def test_artifact_unicode_ok_and_traversal_refused(self):
        self.project.write("inception/requirements-analysis/notes é.md", "hello")
        rel = quote("inception/requirements-analysis/notes é.md")
        resp, body = self.get(f"/api/artifact?record={quote(RECORD_ID)}&path={rel}")
        self.assertEqual((resp.status, json.loads(body)["text"]), (200, "hello"))
        resp, body = self.get(f"/api/artifact?path={quote('../../../../etc/hosts')}")
        self.assertEqual(resp.status, 404)
        self.assertNotIn("/etc", json.loads(body)["error"])

    def test_long_query_refused(self):
        self.assertEqual(self.get("/api/status?x=" + "a" * (server.MAX_QUERY_LEN + 1))[0].status, 414)

    def test_internal_errors_do_not_leak(self):
        original = reader.build_audit
        reader.build_audit = lambda *a: (_ for _ in ()).throw(RuntimeError("/secret/path exploded"))
        try:
            resp, body = self.get("/api/audit")
        finally:
            reader.build_audit = original
        data = json.loads(body)
        self.assertEqual((resp.status, data["error"]), (500, "Internal error"))
        self.assertNotIn("secret", body.decode())

    def test_non_loopback_host_rejected(self):
        with self.assertRaises(SystemExit):
            server.parse_args(["--project", str(self.project.root), "--host", "0.0.0.0"])  # nosec B104 - asserts the bind is refused

    def test_csp_has_no_unsafe_inline(self):
        csp = self.get("/")[0].getheader("Content-Security-Policy")
        self.assertNotIn("unsafe", csp)
        self.assertIn("style-src 'self'", csp)

    def test_stylesheets_served(self):
        for path in ("/app.css", "/cheatsheet.css"):
            resp, body = self.get(path)
            with self.subTest(path=path):
                self.assertEqual(resp.status, 200)
                self.assertTrue(resp.getheader("Content-Type").startswith("text/css"))
                self.assertGreater(len(body), 1000)


class StrictCspSourceTest(unittest.TestCase):
    """Markup and scripts must work under style-src 'self' / script-src 'self' (no inline styles or scripts)."""

    def test_pages_have_no_inline_styles_or_scripts(self):
        for name in ("index.html", "cheatsheet.html"):
            text = (ROOT / "src" / "static" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("<style", text)
                self.assertNotRegex(text, r"\sstyle\s*=")
                self.assertNotRegex(text, r"\son[a-z]+\s*=")
                self.assertNotRegex(text, r"<script(?![^>]*\ssrc=)")

    def test_scripts_never_write_style_attributes_or_html(self):
        for name in ("app.js", "cheatsheet.js"):
            text = (ROOT / "src" / "static" / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotRegex(text, r"setAttribute\(\s*['\"]style")
                self.assertNotRegex(text, r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML")


if __name__ == "__main__":
    unittest.main()
