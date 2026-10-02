"""The same rules over a real loopback HTTP server.

A rule proved at the service layer can still be lost on the way out: a status
that never reaches the wire, a string rendered as a JSON number, a header that
lets a proxy cache a finality figure. These drive the actual socket.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request

from helpers import build, take

from nano_finality.http_app import serve
from nano_finality.samples import LIMIT_MAX


class HttpCase(unittest.TestCase):
    latency_ms = 400
    samples_to_take = 40

    def setUp(self):
        self.service, self.sampler, self.node, self.store, self.clock = build(
            latency_ms=self.latency_ms)
        self.prepare()
        self.httpd = serve(self.service, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    def prepare(self):
        take(self.sampler, self.samples_to_take)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def get(self, path, accept=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if accept:
            req.add_header("Accept", accept)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode(), dict(exc.headers)

    def get_json(self, path):
        status, body, headers = self.get(path)
        return status, json.loads(body), headers


class TestFinalityOverHttp(HttpCase):
    def test_summary_is_public_and_uncacheable(self):
        status, body, headers = self.get_json("/v1/finality")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body["window"], "24h")
        self.assertEqual(body["samples"], 40)
        self.assertEqual(body["node"], "rpc.example-node.org")
        self.assertIn("no fee-based finality signal", body["honest_note"])

    def test_rate_crosses_the_wire_as_a_string(self):
        raw = self.get("/v1/finality")[1]
        self.assertIn('"confirmation_rate": "1.00000"', raw)

    def test_window_parameter(self):
        self.assertEqual(self.get_json("/v1/finality?window=7d")[1]["window"],
                         "7d")
        status, body, _ = self.get_json("/v1/finality?window=12h")
        self.assertEqual((status, body["error"]), (400, "invalid_window"))

    def test_summary_carries_its_own_checkable_urls(self):
        body = self.get_json("/v1/finality")[1]
        self.assertTrue(body["method_url"].endswith("/v1/finality/method"))
        self.assertIn("/v1/finality/samples?window=24h", body["raw_samples_url"])


class TestSamplesOverHttp(HttpCase):
    def test_rows_and_pagination(self):
        status, body, _ = self.get_json("/v1/finality/samples?limit=10")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["samples"]), 10)
        self.assertTrue(body["next_cursor"])

        second = self.get_json(
            f"/v1/finality/samples?limit=10&cursor={body['next_cursor']}")[1]
        first_ids = {r["id"] for r in body["samples"]}
        self.assertFalse(first_ids & {r["id"] for r in second["samples"]})

    def test_limit_bounds(self):
        for bad in ("0", str(LIMIT_MAX + 1), "abc", "-5"):
            status, body, _ = self.get_json(
                f"/v1/finality/samples?limit={bad}")
            self.assertEqual((status, body["error"]), (400, "invalid_limit"),
                             f"limit={bad}")
        self.assertEqual(self.get_json("/v1/finality/samples?limit=1")[0], 200)

    def test_bad_cursor(self):
        status, body, _ = self.get_json("/v1/finality/samples?cursor=%%%")
        self.assertEqual((status, body["error"]), (400, "invalid_cursor"))


class TestMethodOverHttp(HttpCase):
    def test_text_by_default_and_json_on_request(self):
        status, text, headers = self.get("/v1/finality/method")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn("dispatched", text)
        self.assertIn("first reports the block confirmed", text)

        status, body, headers = self.get("/v1/finality/method",
                                         accept="application/json")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("reproduce_it_yourself", json.loads(body))


class TestErrorsOverHttp(HttpCase):
    def test_compare_unconfigured(self):
        status, body, _ = self.get_json("/v1/finality/compare")
        self.assertEqual((status, body["error"]), (404, "not_configured"))
        self.assertNotIn("p50", json.dumps(body))

    def test_unknown_route(self):
        status, body, _ = self.get_json("/v1/nope")
        self.assertEqual((status, body["error"]), (404, "not_found"))

    def test_health(self):
        status, body, _ = self.get_json("/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["sampler_running"])
        self.assertEqual(body["samples_24h"], 40)
        self.assertEqual(body["node"], "reachable")


class TestNeverSampledOverHttp(HttpCase):
    def prepare(self):
        pass                       # the sampler has never run

    def test_503_not_404(self):
        status, body, _ = self.get_json("/v1/finality")
        self.assertEqual((status, body["error"]), (503, "no_samples_yet"))
        self.assertEqual(self.get_json("/v1/health")[1]["sampler_running"],
                         False)


class TestQuietWindowOverHttp(HttpCase):
    samples_to_take = 10

    def prepare(self):
        take(self.sampler, self.samples_to_take)
        self.clock.advance(3 * 3600)

    def test_empty_window_is_200_with_nulls(self):
        status, body, _ = self.get_json("/v1/finality?window=1h")
        self.assertEqual(status, 200)
        self.assertEqual(body["samples"], 0)
        self.assertIsNone(body["elapsed_ms"]["p50"])
        self.assertTrue(body["low_confidence"])
        self.assertEqual(self.get_json("/v1/finality?window=24h")[1]["samples"],
                         10)


if __name__ == "__main__":
    unittest.main()
