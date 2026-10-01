import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from applybot.application_store import ApplicationStore
from applybot.dashboard import TrackerServer

TOKEN = "synthetic-local-test-token-" + "x" * 32


def draft(**changes):
    return {
        "url": "https://jobs.example.test/role",
        "company": "Example",
        "title": "Engineer",
        "status": "queued",
        "notes": "Prepare examples",
        **changes,
    }


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ApplicationStore(Path(self.temp.name) / "tracker.sqlite3")
        self.server = TrackerServer(self.store, 0, token=TOKEN)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def request(
        self,
        method="GET",
        path="/api/applications",
        data=None,
        headers=None,
        authenticated=True,
    ):
        supplied = {"Authorization": "Bearer " + TOKEN} if authenticated else {}
        body = None if data is None else json.dumps(data).encode("utf-8")
        if body is not None:
            supplied["Content-Type"] = "application/json"
        supplied.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=supplied)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_real_http_crud_and_audit(self):
        status, headers, body = self.request("POST", data=draft())
        self.assertEqual(status, 201)
        row = json.loads(body)
        route = headers["Location"]
        self.assertEqual(route, "/api/applications/" + row["id"])
        self.assertEqual(self.request(path=route)[0], 200)
        status, _, body = self.request(
            "PUT", route, {"version": 1, "application": draft(title="Updated")}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], 2)
        self.assertEqual(self.request("DELETE", route, {"version": 2})[0], 200)
        self.assertEqual(self.request(path=route)[0], 404)
        self.assertEqual(
            len(json.loads(self.request(path=route + "/audit")[2])["items"]), 3
        )

    def test_missing_and_wrong_tokens_are_rejected(self):
        self.assertEqual(self.request(authenticated=False)[0], 401)
        self.assertEqual(
            self.request(headers={"Authorization": "Bearer wrong"})[0], 401
        )

    def test_host_and_origin_are_checked(self):
        self.assertEqual(self.request(headers={"Host": "unrelated.test"})[0], 403)
        self.assertEqual(
            self.request(headers={"Origin": "http://unrelated.test"})[0], 403
        )
        self.assertEqual(
            self.request(headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200
        )

    def test_unknown_and_repeated_filters_are_rejected(self):
        for query in ["q=x&q=y", "page=0", "page=1e3", "extra=x", "status=unknown"]:
            with self.subTest(query=query):
                self.assertEqual(
                    self.request(path="/api/applications?" + query)[0], 400
                )

    def test_schema_and_wrapper_unknown_fields_are_rejected(self):
        self.assertEqual(self.request("POST", data={**draft(), "version": 10})[0], 400)
        row = json.loads(self.request("POST", data=draft())[2])
        self.assertEqual(
            self.request(
                "PUT",
                "/api/applications/" + row["id"],
                {"version": 1, "application": draft(), "extra": 1},
            )[0],
            400,
        )
        self.assertEqual(
            self.request("POST", "/api/applications?extra=x", draft())[0], 400
        )

    def test_stale_update_returns_conflict_and_preserves_current_data(self):
        row = json.loads(self.request("POST", data=draft())[2])
        route = "/api/applications/" + row["id"]
        self.assertEqual(
            self.request(
                "PUT", route, {"version": 1, "application": draft(title="Winner")}
            )[0],
            200,
        )
        status, headers, body = self.request(
            "PUT", route, {"version": 1, "application": draft(title="Loser")}
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["requestId"], headers["X-Request-Id"])
        self.assertEqual(json.loads(self.request(path=route)[2])["title"], "Winner")

    def test_store_failure_has_no_partial_write_or_internal_error_text(self):
        row = json.loads(self.request("POST", data=draft())[2])
        with self.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_audit BEFORE INSERT ON application_audit BEGIN SELECT RAISE(ABORT,'internal SQL details'); END"
            )
        route = "/api/applications/" + row["id"]
        status, _, body = self.request(
            "PUT", route, {"version": 1, "application": draft(title="Rejected")}
        )
        self.assertEqual(status, 503)
        self.assertNotIn(b"internal SQL", body)
        self.assertEqual(json.loads(self.request(path=route)[2]), row)

    def test_request_size_and_content_type_limits(self):
        self.assertEqual(self.request("POST", data={"notes": "x" * 65537})[0], 413)
        self.assertEqual(
            self.request("POST", data=draft(), headers={"Content-Type": "text/plain"})[
                0
            ],
            415,
        )

    def test_static_allowlist_and_response_headers(self):
        status, headers, body = self.request(path="/", authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Application tracker", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request(path="/profile.yaml")[0], 404)
        self.assertEqual(self.request(path="/../profile.yaml")[0], 404)

    def test_sql_like_search_is_literal_over_http(self):
        self.request("POST", data=draft(title="100% engineer"))
        self.request("POST", data=draft(title="100X engineer"))
        status, _, body = self.request(path="/api/applications?q=%25")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 1)
