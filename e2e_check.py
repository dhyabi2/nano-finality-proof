#!/usr/bin/env python3
"""End-to-end check: all 12 numbered spec tests, in order, over real HTTP.

The unit suite drives the service objects. This drives a socket: it starts the
server on loopback, runs the sampler against a fake node on virtual time, and
checks every numbered test from
the finality-proof design spec plus every row of the error
table, from the outside, as an agent reading the service would see it.

    python3 e2e_check.py

No Nano node is needed and nothing leaves the machine.
"""

import json
import sys
import threading
import urllib.error
import urllib.request

from nano_finality.clock import FakeClock
from nano_finality.http_app import serve
from nano_finality.node import FakeNode
from nano_finality.sampler import Sampler
from nano_finality.samples import SampleStore
from nano_finality.service import Service

PASSED = FAILED = 0
SOURCE = "nano_1source_account_for_the_sampler"
DEST = "nano_1destination_account_for_the_sampler"


def check(label, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}  {detail}")


def build(latency_ms, base="http://127.0.0.1"):
    clock = FakeClock()
    node = FakeNode(clock, latency_ms=latency_ms, name="rpc.example-node.org")
    store = SampleStore(clock)
    sampler = Sampler(node, store, clock, SOURCE, DEST)
    service = Service(sampler, store, clock, base_url=base)
    return service, sampler, node, store, clock


def take(sampler, n, gap_s=60):
    for _ in range(n):
        sampler.run_once()
        sampler.clock.advance(gap_s)


class Server:
    def __init__(self, service):
        self.httpd = serve(service, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def get(self, path, accept=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if accept:
            req.add_header("Accept", accept)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def json(self, path, accept="application/json"):
        status, body = self.get(path, accept=accept)
        return status, json.loads(body)


def main():
    print("END-TO-END CHECK -- nano-finality-proof")
    print("Every request below crosses a real socket on loopback.\n")

    # ---- 1 ---------------------------------------------------------------
    print("1. test_percentiles_are_measured_not_constant")
    service, sampler, node, store, clock = build(1500)
    take(sampler, 100)
    with Server(service) as s:
        body = s.json("/v1/finality?window=24h")[1]
        check("100 samples at 1500ms -> p50 is 1500", body["elapsed_ms"]["p50"] == 1500,
              body["elapsed_ms"])
        node.latency_ms = 300
        take(sampler, 100)
        moved = s.json("/v1/finality?window=1h")[1]
        check("node drops to 300ms -> the 1h window moves to 300",
              moved["elapsed_ms"]["p50"] == 300, moved["elapsed_ms"])
        wide = s.json("/v1/finality?window=24h")[1]["elapsed_ms"]
        check("the 24h window still spans both", (wide["min"], wide["max"]) == (300, 1500),
              wide)

    # ---- 2 ---------------------------------------------------------------
    print("\n2. test_timeouts_are_in_the_denominator")
    service, sampler, node, store, clock = build(400)
    take(sampler, 98)
    node.latency_ms = 99_999
    take(sampler, 2)
    with Server(service) as s:
        body = s.json("/v1/finality")[1]
        check("samples == 100", body["samples"] == 100, body["samples"])
        check("confirmed == 98", body["confirmed"] == 98, body["confirmed"])
        check("timeouts == 2", body["timeouts"] == 2, body["timeouts"])
        check('confirmation_rate == "0.98000"',
              body["confirmation_rate"] == "0.98000", body["confirmation_rate"])
        check("samples != confirmed  (the flattering bug)",
              body["samples"] != body["confirmed"])
        check("percentiles are over the 98 that confirmed",
              body["elapsed_ms"]["p50"] == 400 and body["elapsed_ms"]["max"] == 400,
              body["elapsed_ms"])

    # ---- 3 ---------------------------------------------------------------
        print("\n3. test_timeout_samples_are_never_deleted")
        rows = s.json("/v1/finality/samples?limit=1000")[1]["samples"]
        timeouts = [r for r in rows if r["outcome"] == "timeout"]
        check("both timeouts are served in the raw rows", len(timeouts) == 2,
              len(timeouts))
        check("a timeout has elapsed_ms null",
              all(r["elapsed_ms"] is None for r in timeouts))
        check("a timeout carries the cutoff that was applied",
              all(r["timeout_ms"] == 30_000 for r in timeouts))
        check("the raw rows hold every sample", len(rows) == 100, len(rows))

    # ---- 4 ---------------------------------------------------------------
    print("\n4. test_low_confidence_flag_on_small_n")
    service, sampler, node, store, clock = build(400)
    take(sampler, 5)
    with Server(service) as s:
        body = s.json("/v1/finality")[1]
        check("low_confidence is set", body.get("low_confidence") is True)
        check("the reason names the threshold",
              body.get("low_confidence_reason") == "fewer than 30 samples in this window",
              body.get("low_confidence_reason"))
        check("the figure is still served", body["elapsed_ms"]["p50"] == 400)
        check("the window was not silently widened", body["window"] == "24h")

    # ---- 5 ---------------------------------------------------------------
    print("\n5. test_zero_samples_is_200_not_404")
    service, sampler, node, store, clock = build(400)
    take(sampler, 10)
    clock.advance(3 * 3600)
    with Server(service) as s:
        status, body = s.json("/v1/finality?window=1h")
        check("empty window is 200", status == 200, status)
        check("samples == 0", body["samples"] == 0)
        check("every percentile is null",
              set(body["elapsed_ms"].values()) == {None}, body["elapsed_ms"])
        check("low_confidence is set", body.get("low_confidence") is True)
        check("the 24h window still has the ten",
              s.json("/v1/finality?window=24h")[1]["samples"] == 10)

    # ---- 6 ---------------------------------------------------------------
    print("\n6. test_never_sampled_is_503")
    fresh, fresh_sampler, _, _, _ = build(400)
    with Server(fresh) as s:
        status, body = s.json("/v1/finality")
        check("never sampled is 503", status == 503, status)
        check("the code is no_samples_yet", body["error"] == "no_samples_yet",
              body)
        check("distinguishable from the quiet window above (200/samples:0)",
              status == 503)
        check("health says the sampler is not running",
              s.json("/v1/health")[1]["sampler_running"] is False)

    # ---- 7 ---------------------------------------------------------------
    print("\n7. test_rate_is_a_decimal_string")
    service, sampler, node, store, clock = build(400)
    take(sampler, 1434)
    node.latency_ms = 99_999
    take(sampler, 2)
    with Server(service) as s:
        status, raw = s.get("/v1/finality?window=30d")
        body = json.loads(raw)
        check("1436 samples, 1434 confirmed",
              (body["samples"], body["confirmed"]) == (1436, 1434),
              (body["samples"], body["confirmed"]))
        check('rate renders "0.99861", not 0.9986072...',
              body["confirmation_rate"] == "0.99861", body["confirmation_rate"])
        check("it is a JSON string on the wire",
              '"confirmation_rate": "0.99861"' in raw)
        check("exactly five decimal places",
              len(body["confirmation_rate"].split(".")[1]) == 5)

    # ---- 8 ---------------------------------------------------------------
        print("\n8. test_honest_note_is_present_and_concedes  (the overclaim test)")
        lowered = raw.lower()
        check('the body concedes "no fee-based finality signal"',
              "no fee-based finality signal" in lowered)
        for phrase in ("instant finality guaranteed", "as secure as",
                       "more secure than", "cannot be reversed"):
            check(f"the body never says {phrase!r}", phrase not in lowered)
        method_text = s.get("/v1/finality/method")[1].lower()
        for phrase in ("instant finality guaranteed", "as secure as",
                       "more secure than", "cannot be reversed"):
            check(f"the method never says {phrase!r}", phrase not in method_text)

    # ---- 9 ---------------------------------------------------------------
        print("\n9. test_method_endpoint_has_a_runnable_command")
        status, text = s.get("/v1/finality/method")
        check("method is 200 text/plain", status == 200)
        check('it states timing starts at "dispatched"', "dispatched" in text)
        check('it states timing stops at "first reports the block confirmed"',
              "first reports the block confirmed" in text)
        check("it ends with a command the reader can run",
              "curl" in text and "block_info" in text)
        status, mjson = s.json("/v1/finality/method")
        check("the JSON form reports the running configuration",
              mjson["timeout_ms"] == 30_000 and mjson["node"] == "rpc.example-node.org",
              (mjson["timeout_ms"], mjson["node"]))
        check("it says nothing is excluded from the denominator",
              mjson["exclusions"]["from_the_denominator"] == "nothing")

    # ---- 10 --------------------------------------------------------------
        print("\n10. test_samples_expose_block_hashes")
        page = s.json("/v1/finality/samples?window=30d&limit=1000")[1]
        confirmed = [r for r in page["samples"] if r["outcome"] == "confirmed"]
        check("a full page of rows came back", len(page["samples"]) == 1000,
              len(page["samples"]))
        check("every confirmed row carries a 64-hex block hash",
              all(len(r["block_hash"]) == 64
                  and all(c in "0123456789ABCDEF" for c in r["block_hash"])
                  for r in confirmed))
        check("the hashes are distinct, so a reader can check a hundred",
              len({r["block_hash"] for r in confirmed}) == len(confirmed))
        check("every row carries the amount as an exact raw string",
              all(r["amount_raw"] == "1" for r in page["samples"]))

    # ---- 11 --------------------------------------------------------------
    print("\n11. test_compare_unconfigured_is_404_not_fabricated")
    service, sampler, node, store, clock = build(400)
    take(sampler, 20)
    with Server(service) as s:
        status, body = s.json("/v1/finality/compare")
        check("unconfigured compare is 404", status == 404, status)
        check("the code is not_configured", body["error"] == "not_configured")
        check("no second column was synthesised", "p50" not in json.dumps(body))

    second_clock = clock
    second_node = FakeNode(second_clock, latency_ms=900, name="rpc.other-node.org")
    second_store = SampleStore(second_clock)
    second = Sampler(second_node, second_store, second_clock, SOURCE, DEST)
    take(second, 20)
    service.compare_sampler, service.compare_store = second, second_store
    with Server(service) as s:
        body = s.json("/v1/finality/compare")[1]
        check("configured compare returns two named nodes",
              [n["node"] for n in body["nodes"]]
              == ["rpc.example-node.org", "rpc.other-node.org"],
              [n["node"] for n in body["nodes"]])
        check("each node keeps its own figures",
              (body["nodes"][0]["elapsed_ms"]["p50"],
               body["nodes"][1]["elapsed_ms"]["p50"]) == (400, 900),
              [n["elapsed_ms"]["p50"] for n in body["nodes"]])
        check("the two are never merged into one column",
              "elapsed_ms" not in body and "confirmation_rate" not in body)

    # ---- 12 --------------------------------------------------------------
    print("\n12. test_p99_includes_the_slow_tail")
    service, sampler, node, store, clock = build(400)
    take(sampler, 99)
    node.latency_ms = 8000
    take(sampler, 1)
    with Server(service) as s:
        elapsed = s.json("/v1/finality")[1]["elapsed_ms"]
        check("max == 8000", elapsed["max"] == 8000, elapsed)
        check("p99 reflects the tail, not 400", elapsed["p99"] > 400, elapsed)
        check("p50 is still 400", elapsed["p50"] == 400, elapsed)
        rows = s.json("/v1/finality/samples?limit=1000")[1]["samples"]
        check("the slow sample is in the raw rows too",
              8000 in [r["elapsed_ms"] for r in rows])

    # ---- the error table -------------------------------------------------
        print("\nThe error table, every row")
        for path, status, code in (
            ("/v1/finality?window=12h", 400, "invalid_window"),
            ("/v1/finality/samples?window=3d", 400, "invalid_window"),
            ("/v1/finality/samples?limit=0", 400, "invalid_limit"),
            ("/v1/finality/samples?limit=1001", 400, "invalid_limit"),
            ("/v1/finality/samples?cursor=%%%", 400, "invalid_cursor"),
            ("/v1/finality/compare", 404, "not_configured"),
            ("/v1/nope", 404, "not_found"),
        ):
            got_status, got = s.json(path)
            check(f"{path} -> {status} {code}",
                  (got_status, got.get("error")) == (status, code),
                  (got_status, got.get("error")))
    with Server(build(400)[0]) as s:
        got_status, got = s.json("/v1/finality")
        check("a store that never held a sample -> 503 no_samples_yet",
              (got_status, got["error"]) == (503, "no_samples_yet"),
              (got_status, got.get("error")))

    print()
    if FAILED:
        print(f"{FAILED} CHECK(S) FAILED, {PASSED} passed")
        return 1
    print(f"ALL {PASSED} END-TO-END CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
