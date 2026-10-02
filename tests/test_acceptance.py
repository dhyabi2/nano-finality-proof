"""The 12 numbered tests from the finality-proof design spec,
in order, plus the error table and the rules stated in prose beside them.

Not one of them needs a Nano node: the node sits behind `NanoNode` and every
test drives `FakeNode` on a `FakeClock`, so a 30-second timeout and a 30-day
retention window both resolve in microseconds.
"""

import ast
import json
import os
import unittest

from helpers import BASE_URL, build, take

from nano_finality import errors, samples, stats
from nano_finality.clock import FakeClock
from nano_finality.node import FakeNode, NodeError
from nano_finality.sampler import Sampler
from nano_finality.samples import SampleStore
from nano_finality.service import Service

def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "nano_finality")


class Test01PercentilesAreMeasured(unittest.TestCase):
    """1. test_percentiles_are_measured_not_constant."""

    def test_percentiles_follow_the_node(self):
        service, sampler, node, store, clock = build(latency_ms=1500)
        take(sampler, 100)
        self.assertEqual(service.finality("24h")["elapsed_ms"]["p50"], 1500)

        node.latency_ms = 300
        take(sampler, 100)
        # The 1h window now holds only the 300ms samples, so it moved.
        self.assertEqual(service.finality("1h")["elapsed_ms"]["p50"], 300)
        # And the 24h window, which still holds both, sits between them.
        wide = service.finality("24h")["elapsed_ms"]
        self.assertGreater(wide["p99"], 300)
        self.assertEqual(wide["min"], 300)
        self.assertEqual(wide["max"], 1500)

    def test_no_confirmation_time_constant_exists_in_the_source(self):
        """Grep the package for a number that could be a baked-in latency.

        Every integer literal in the package that falls in the range a
        confirmation time plausibly occupies (100..30000 ms) has to be named
        here with a reason it is not one. A constant that creeps in
        unannounced fails this test, which is the point: the spec's first
        non-negotiable is that no figure is ever hard-coded.
        """
        allowed = {
            200: "an HTTP status code",
            400: "an HTTP status code",
            404: "an HTTP status code",
            503: "an HTTP status code",
            100: "per cent -- the unit percentile ranks are expressed in",
            3600: "seconds in the 1h window",
            86_400: "seconds in the 24h window",
            604_800: "seconds in the 7d window",
            2_592_000: "seconds in the 30d retention window",
            30_000: "the sample TIMEOUT default, a cutoff we apply, not a "
                    "latency we measure",
            1000: "milliseconds per second, and the samples page limit ceiling",
            8080: "the default HTTP port",
            1_800_000_000: "the FakeClock epoch, a date and not a duration",
            7076: "the Nano node's default RPC port, inside an example command",
        }
        offenders = []
        for name in sorted(os.listdir(PKG)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(PKG, name)
            tree = ast.parse(read(path), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                v = node.value
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if 100 <= v <= 30_000 and v not in allowed:
                    offenders.append(f"{name}:{node.lineno} -> {v}")
        self.assertEqual(offenders, [], "unexplained numeric literal in the "
                         "confirmation-time range: " + ", ".join(offenders))

    def test_no_output_field_is_ever_a_literal(self):
        """The output figures come from `stats`, and `stats` holds no latency."""
        tree = ast.parse(read(os.path.join(PKG, "stats.py")))
        literals = {n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, (int, float))
                    and not isinstance(n.value, bool)}
        self.assertTrue(literals <= {0, 1, 5, 50, 90, 95, 99, 100},
                        f"stats.py carries an unexpected number: {literals}")


class Test02TimeoutsInTheDenominator(unittest.TestCase):
    """2. test_timeouts_are_in_the_denominator -- the flattering bug."""

    def test_rate_counts_timeouts_and_percentiles_do_not(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 98)
        node.latency_ms = 99_999          # past the 30s cutoff
        take(sampler, 2)

        body = service.finality("24h")
        self.assertEqual(body["samples"], 100)
        self.assertEqual(body["confirmed"], 98)
        self.assertEqual(body["timeouts"], 2)
        self.assertEqual(body["errors"], 0)
        self.assertEqual(body["confirmation_rate"], "0.98000")
        # The test that catches the flattering bug:
        self.assertNotEqual(body["samples"], body["confirmed"])
        # Percentiles over the 98 that confirmed, all at 400ms.
        self.assertEqual(body["elapsed_ms"]["p50"], 400)
        self.assertEqual(body["elapsed_ms"]["max"], 400)

    def test_errors_are_in_the_denominator_too(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 9)
        node.fail_with = "node refused the send"
        take(sampler, 1)
        body = service.finality("24h")
        self.assertEqual((body["samples"], body["confirmed"], body["errors"]),
                         (10, 9, 1))
        self.assertEqual(body["confirmation_rate"], "0.90000")


class Test03TimeoutsAreNeverDeleted(unittest.TestCase):
    """3. test_timeout_samples_are_never_deleted."""

    def test_a_timeout_is_served_in_the_raw_rows(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 3)
        node.latency_ms = 99_999
        take(sampler, 1)

        rows = service.samples_page("24h")["samples"]
        self.assertEqual(len(rows), 4)
        timeouts = [r for r in rows if r["outcome"] == "timeout"]
        self.assertEqual(len(timeouts), 1)
        self.assertIsNone(timeouts[0]["elapsed_ms"])
        self.assertIsNone(timeouts[0]["confirmed_at"])
        self.assertEqual(timeouts[0]["timeout_ms"], 30_000)
        self.assertIn("not confirmed", timeouts[0]["error"])

    def test_prune_does_not_favour_confirmations(self):
        """Retention drops by age, never by outcome."""
        service, sampler, node, store, clock = build(latency_ms=400)
        node.latency_ms = 99_999
        take(sampler, 1)                      # a timeout, then 40 days pass
        node.latency_ms = 400
        clock.advance(40 * 86_400)
        take(sampler, 1)
        self.assertEqual(len(store.all_rows()), 1)   # by age only

        node.latency_ms = 99_999
        take(sampler, 1)
        outcomes = sorted(r.outcome for r in store.all_rows())
        self.assertEqual(outcomes, ["confirmed", "timeout"])


class Test04LowConfidence(unittest.TestCase):
    """4. test_low_confidence_flag_on_small_n."""

    def test_five_samples_are_flagged_and_still_served(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 5)
        body = service.finality("24h")
        self.assertTrue(body["low_confidence"])
        self.assertEqual(body["low_confidence_reason"],
                         "fewer than 30 samples in this window")
        self.assertEqual(body["samples"], 5)
        self.assertEqual(body["elapsed_ms"]["p50"], 400)      # still served
        self.assertEqual(body["window"], "24h")               # not widened

    def test_thirty_samples_is_not_flagged(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 30)
        body = service.finality("24h")
        self.assertNotIn("low_confidence", body)

    def test_the_window_is_never_widened_to_reach_a_nicer_n(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 90)                     # 90 minutes of history
        hour = service.finality("1h")
        day = service.finality("24h")
        self.assertLess(hour["samples"], day["samples"])
        self.assertEqual(hour["window"], "1h")


class Test05ZeroSamplesIs200(unittest.TestCase):
    """5. test_zero_samples_is_200_not_404."""

    def test_empty_window_over_a_populated_store(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 10)
        clock.advance(2 * 3600)               # nothing in the last hour

        body = service.finality("1h")
        self.assertEqual(body["samples"], 0)
        self.assertEqual(body["confirmed"], 0)
        self.assertTrue(body["low_confidence"])
        self.assertEqual(set(body["elapsed_ms"].values()), {None})
        self.assertEqual(body["confirmation_rate"], "0.00000")
        # and the 24h window still has the ten, so nothing was deleted
        self.assertEqual(service.finality("24h")["samples"], 10)


class Test06NeverSampledIs503(unittest.TestCase):
    """6. test_never_sampled_is_503, distinguishable from test 5."""

    def test_untouched_store_is_a_hard_failure(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        with self.assertRaises(errors.ApiError) as caught:
            service.finality("24h")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(caught.exception.code, "no_samples_yet")

    def test_the_two_empty_cases_are_not_the_same(self):
        never, sampler_a, _, _, _ = build()
        populated, sampler_b, _, _, clock_b = build()
        take(sampler_b, 1)
        clock_b.advance(2 * 3600)

        with self.assertRaises(errors.ApiError) as caught:
            never.finality("1h")
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(populated.finality("1h")["samples"], 0)   # a 200

    def test_a_store_pruned_back_to_empty_is_still_not_503(self):
        """Sampling for a month and going quiet is not a broken sampler."""
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 1)
        clock.advance(60 * 86_400)
        store.prune()
        self.assertEqual(store.all_rows(), [])
        self.assertEqual(service.finality("24h")["samples"], 0)


class Test07RateIsADecimalString(unittest.TestCase):
    """7. test_rate_is_a_decimal_string."""

    def test_type_and_precision(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 1434)
        node.latency_ms = 99_999
        take(sampler, 2)

        body = service.finality("30d")
        self.assertEqual(body["samples"], 1436)
        rate = body["confirmation_rate"]
        self.assertIsInstance(rate, str)
        self.assertNotIsInstance(rate, float)
        self.assertEqual(rate, "0.99861")
        self.assertEqual(len(rate.split(".")[1]), 5)
        # and it survives a JSON round trip as a string, not a number
        self.assertIsInstance(json.loads(json.dumps(body))
                              ["confirmation_rate"], str)

    def test_it_is_never_rendered_through_a_float(self):
        self.assertEqual(stats.confirmation_rate(1434, 1436), "0.99861")
        self.assertEqual(stats.confirmation_rate(1, 3), "0.33333")
        self.assertEqual(stats.confirmation_rate(2, 3), "0.66667")
        self.assertEqual(stats.confirmation_rate(1, 1), "1.00000")
        self.assertEqual(stats.confirmation_rate(0, 7), "0.00000")


class Test08HonestNote(unittest.TestCase):
    """8. test_honest_note_is_present_and_concedes -- the overclaim test."""

    FORBIDDEN = ("instant finality guaranteed", "as secure as",
                 "more secure than", "cannot be reversed")

    def test_the_note_concedes_and_never_overclaims(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 5)
        body = json.dumps(service.finality("24h")).lower()
        self.assertIn("no fee-based finality signal", body)
        for phrase in self.FORBIDDEN:
            self.assertNotIn(phrase, body, f"overclaim in the body: {phrase!r}")

    def test_no_surface_of_this_service_overclaims(self):
        """The same rule on the method, the samples page and the README."""
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 5)
        root = os.path.dirname(PKG)
        blobs = [json.dumps(service.finality("24h")),
                 json.dumps(service.samples_page("24h")),
                 service.method_text(),
                 json.dumps(service.method_json()),
                 read(os.path.join(root, "README.md"))]
        for name in sorted(os.listdir(PKG)):
            if name.endswith(".py"):
                blobs.append(read(os.path.join(PKG, name)))
        for blob in blobs:
            for phrase in self.FORBIDDEN:
                self.assertNotIn(phrase, blob.lower(),
                                 f"overclaim {phrase!r} in a published surface")


class Test09MethodIsRunnable(unittest.TestCase):
    """9. test_method_endpoint_has_a_runnable_command."""

    def test_timing_definition_and_a_command(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        text = service.method_text()
        self.assertIn("dispatched", text)
        self.assertIn("first reports the block confirmed", text)
        self.assertIn("curl", text)
        self.assertIn("block_info", text)
        self.assertIn("$NODE", text)

    def test_the_method_reports_the_running_configuration(self):
        """Not a document that can drift: the figures are the sampler's own."""
        service, sampler, node, store, clock = build(
            latency_ms=400, timeout_ms=12_345, interval_s=17)
        data = service.method_json()
        self.assertEqual(data["timeout_ms"], 12_345)
        self.assertEqual(data["interval_s"], 17)
        self.assertEqual(data["node"], "rpc.example-node.org")
        self.assertEqual(data["retention_days"], 30)
        self.assertEqual(data["confirmation_rate"]["denominator"][:3], "ALL")
        self.assertEqual(data["exclusions"]["from_the_denominator"], "nothing")


class Test10SamplesExposeBlockHashes(unittest.TestCase):
    """10. test_samples_expose_block_hashes."""

    def test_every_confirmed_sample_carries_a_64_hex_hash(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 25)
        rows = service.samples_page("24h")["samples"]
        confirmed = [r for r in rows if r["outcome"] == "confirmed"]
        self.assertEqual(len(confirmed), 25)
        for row in confirmed:
            self.assertRegex(row["block_hash"], r"^[0-9A-F]{64}$")
            self.assertRegex(row["id"], r"^smp_[0-9a-f]{16}$")
            self.assertEqual(row["amount_raw"], "1")
            self.assertTrue(row["sent_at"].endswith("Z"))
            self.assertIsNotNone(row["confirmed_at"])

    def test_hashes_are_distinct_so_a_reader_can_check_a_hundred(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 100)
        rows = service.samples_page("24h", limit=100)["samples"]
        self.assertEqual(len({r["block_hash"] for r in rows}), 100)


class Test11CompareIsNeverFabricated(unittest.TestCase):
    """11. test_compare_unconfigured_is_404_not_fabricated."""

    def test_unconfigured_is_404(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 5)
        with self.assertRaises(errors.ApiError) as caught:
            service.compare("24h")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.code, "not_configured")

    def test_no_second_column_is_synthesised(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 5)
        try:
            service.compare("24h")
        except errors.ApiError as exc:
            self.assertNotIn("p50", json.dumps(exc.body()))
        self.assertIsNone(service.compare_store)

    def test_configured_compare_reports_both_nodes_separately(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        second_node = FakeNode(clock, latency_ms=900, name="rpc.other-node.org")
        second_store = SampleStore(clock)
        second = Sampler(second_node, second_store, clock,
                         sampler.source, sampler.destination)
        service.compare_sampler, service.compare_store = second, second_store
        take(sampler, 10)
        take(second, 10)

        body = service.compare("24h")
        self.assertEqual(len(body["nodes"]), 2)
        self.assertEqual(body["nodes"][0]["node"], "rpc.example-node.org")
        self.assertEqual(body["nodes"][1]["node"], "rpc.other-node.org")
        self.assertEqual(body["nodes"][0]["elapsed_ms"]["p50"], 400)
        self.assertEqual(body["nodes"][1]["elapsed_ms"]["p50"], 900)
        # No merged column: the two nodes' figures are never combined into
        # one, and the top level carries no figure of its own to combine them
        # into.
        self.assertNotIn("elapsed_ms", body)
        self.assertNotIn("confirmation_rate", body)
        self.assertNotEqual(body["nodes"][0]["elapsed_ms"],
                            body["nodes"][1]["elapsed_ms"])


class Test12P99IncludesTheSlowTail(unittest.TestCase):
    """12. test_p99_includes_the_slow_tail."""

    def test_one_slow_sample_in_a_hundred_shows(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 99)
        node.latency_ms = 8000
        take(sampler, 1)

        elapsed = service.finality("24h")["elapsed_ms"]
        self.assertEqual(elapsed["max"], 8000)
        self.assertGreater(elapsed["p99"], 400)
        self.assertEqual(elapsed["min"], 400)
        self.assertEqual(elapsed["p50"], 400)

    def test_the_tail_is_also_in_the_raw_rows(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 99)
        node.latency_ms = 8000
        take(sampler, 1)
        rows = service.samples_page("24h", limit=1000)["samples"]
        self.assertIn(8000, [r["elapsed_ms"] for r in rows])


class TestErrorTable(unittest.TestCase):
    """Every row of the spec's error table, at the service layer."""

    def setUp(self):
        self.service, self.sampler, _, _, _ = build(latency_ms=400)
        take(self.sampler, 5)

    def _raises(self, status, code, fn, *a, **kw):
        with self.assertRaises(errors.ApiError) as caught:
            fn(*a, **kw)
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (status, code))

    def test_unknown_window(self):
        self._raises(400, "invalid_window", self.service.finality, "12h")
        self._raises(400, "invalid_window", self.service.samples_page, "3d")

    def test_limit_outside_range(self):
        self._raises(400, "invalid_limit", self.service.samples_page,
                     "24h", limit=0)
        self._raises(400, "invalid_limit", self.service.samples_page,
                     "24h", limit=1001)
        self._raises(400, "invalid_limit", self.service.samples_page,
                     "24h", limit=True)

    def test_malformed_cursor(self):
        self._raises(400, "invalid_cursor", self.service.samples_page,
                     "24h", cursor="not-base64!!")
        self._raises(400, "invalid_cursor", self.service.samples_page,
                     "24h", cursor=samples.encode_cursor("smp_ffffffffffffffff"))

    def test_compare_not_configured(self):
        self._raises(404, "not_configured", self.service.compare)

    def test_sampler_never_ran(self):
        fresh, _, _, _, _ = build()
        self._raises(503, "no_samples_yet", fresh.finality)


class TestPagination(unittest.TestCase):
    def test_cursor_walks_every_row_exactly_once(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 25)
        seen, cursor = [], None
        while True:
            page = service.samples_page("24h", limit=10, cursor=cursor)
            seen += [r["id"] for r in page["samples"]]
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(len(seen), 25)
        self.assertEqual(len(set(seen)), 25)
        self.assertEqual(seen, [r.id for r in store.all_rows()])

    def test_last_page_has_no_cursor(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        take(sampler, 3)
        page = service.samples_page("24h", limit=10)
        self.assertIsNone(page["next_cursor"])


class TestDurability(unittest.TestCase):
    def test_samples_survive_a_restart(self):
        """A 24-hour run must not restart its clock because a process did."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            clock = FakeClock()
            service, sampler, node, store, _ = build(latency_ms=400,
                                                     clock=clock,
                                                     store_path=path)
            take(sampler, 7)
            node.latency_ms = 99_999
            take(sampler, 1)

            reloaded = SampleStore(clock, path=path)
            self.assertEqual(len(reloaded.all_rows()), 8)
            self.assertTrue(reloaded.ever_sampled)
            outcomes = [r.outcome for r in reloaded.all_rows()]
            self.assertEqual(outcomes.count("timeout"), 1)

            revived = Service(sampler, reloaded, clock, base_url=BASE_URL)
            self.assertEqual(revived.finality("24h")["confirmation_rate"],
                             "0.87500")

    def test_a_torn_final_line_does_not_lose_the_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            clock = FakeClock()
            _, sampler, _, _, _ = build(latency_ms=400, clock=clock,
                                        store_path=path)
            take(sampler, 4)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write('{"id": "smp_dead')        # killed mid-write
            self.assertEqual(len(SampleStore(clock, path=path).all_rows()), 4)


class TestHealth(unittest.TestCase):
    def test_health_before_and_after_the_first_sample(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        before = service.health()
        self.assertFalse(before["sampler_running"])
        self.assertIsNone(before["last_sample_at"])
        self.assertEqual(before["samples_24h"], 0)
        self.assertEqual(before["node"], "reachable")

        take(sampler, 3)
        after = service.health()
        self.assertTrue(after["sampler_running"])
        self.assertEqual(after["samples_24h"], 3)
        self.assertEqual(after["last_sample_at"], store.last().sent_at)

    def test_unreachable_node_is_reported(self):
        service, sampler, node, store, clock = build(latency_ms=400)
        node.up = False
        self.assertEqual(service.health()["node"], "unreachable")


class TestTimingDefinition(unittest.TestCase):
    def test_elapsed_is_measured_from_dispatch_not_from_return(self):
        """A slow send RPC is inside the measurement, not outside it."""
        clock = FakeClock()

        class SlowSend(FakeNode):
            def send(self, source, destination, amount_raw):
                self.clock.advance(0.2)        # 200ms spent in the send call
                return super().send(source, destination, amount_raw)

        node = SlowSend(clock, latency_ms=400, name="slow.example.org")
        store = SampleStore(clock)
        sampler = Sampler(node, store, clock, "nano_a", "nano_b")
        sampler.run_once()
        # 200ms of send plus 400ms to confirm, counted from dispatch.
        self.assertEqual(store.last().elapsed_ms, 600)

    def test_a_node_error_mid_poll_is_an_error_row_not_a_lost_sample(self):
        clock = FakeClock()

        class DiesOnPoll(FakeNode):
            def confirmed(self, block_hash):
                raise NodeError("node went away")

        store = SampleStore(clock)
        sampler = Sampler(DiesOnPoll(clock, 400), store, clock, "a", "b")
        sampler.run_once()
        self.assertEqual(store.last().outcome, "error")
        self.assertIsNone(store.last().elapsed_ms)
        self.assertIsNotNone(store.last().block_hash)


class TestNoNetworkInTheTests(unittest.TestCase):
    def test_only_node_py_can_dial_out(self):
        """One module can open an outbound connection, and no test builds it.

        `urllib.parse` and `http.server` are deliberately not on this list:
        the first parses strings and the second listens. What is pinned here
        is that nothing but `node.py` can *dial out*, which is what would make
        a test depend on a live Nano node.
        """
        importers = []
        for name in sorted(os.listdir(PKG)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(read(os.path.join(PKG, name)))
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                outbound = {"urllib.request", "urllib.error", "socket",
                            "http.client", "requests", "httpx"}
                if any(m in outbound or m.split(".")[0] in {"socket", "requests",
                                                            "httpx"}
                       for m in mods):
                    importers.append(name)
        self.assertEqual(sorted(set(importers)), ["node.py"])


if __name__ == "__main__":
    unittest.main()
