"""The HTTP surface: stdlib only, no framework, no dependency to audit.

Everything here is public and unauthenticated by design. There is no token to
leak because there is no endpoint that takes one: a finality figure an outside
agent has to ask permission to read is a figure it will not read.
"""

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import errors, samples


def make_handler(service):
    routes = [
        ("GET", re.compile(r"^/v1/health$"), "health"),
        ("GET", re.compile(r"^/v1/finality$"), "finality"),
        ("GET", re.compile(r"^/v1/finality/samples$"), "samples"),
        ("GET", re.compile(r"^/v1/finality/method$"), "method"),
        ("GET", re.compile(r"^/v1/finality/compare$"), "compare"),
    ]

    class Handler(BaseHTTPRequestHandler):
        server_version = "nano-finality-proof"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def _send(self, status, payload,
                  content_type="application/json; charset=utf-8"):
            if isinstance(payload, (dict, list)):
                raw = json.dumps(payload, indent=1).encode("utf-8")
            else:
                raw = str(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            for verb, pattern, name in routes:
                if verb != "GET" or not pattern.match(parsed.path):
                    continue
                try:
                    getattr(self, f"_do_{name}")(query)
                except errors.ApiError as exc:
                    self._send(exc.status, exc.body())
                return
            self._send(404, errors.not_found().body())

        # -- handlers ----------------------------------------------------
        def _window(self, query):
            return (query.get("window") or [samples.DEFAULT_WINDOW])[0]

        def _do_health(self, query):
            self._send(200, service.health())

        def _do_finality(self, query):
            self._send(200, service.finality(self._window(query)))

        def _do_samples(self, query):
            raw_limit = (query.get("limit") or [str(samples.LIMIT_DEFAULT)])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                raise errors.invalid_limit(samples.LIMIT_MIN,
                                           samples.LIMIT_MAX) from None
            self._send(200, service.samples_page(
                self._window(query), limit=limit,
                cursor=(query.get("cursor") or [None])[0]))

        def _do_method(self, query):
            accept = (self.headers.get("Accept") or "").lower()
            if "application/json" in accept:
                self._send(200, service.method_json())
            else:
                self._send(200, service.method_text(),
                           content_type="text/plain; charset=utf-8")

        def _do_compare(self, query):
            self._send(200, service.compare(self._window(query)))

    return Handler


def serve(service, host="127.0.0.1", port=8080):
    return ThreadingHTTPServer((host, port), make_handler(service))


def build_from_env():                          # pragma: no cover - entry point
    """Wire a real service from the environment. Nothing is read from a file."""
    from .clock import Clock
    from .node import RpcNode
    from .sampler import from_env
    from .service import Service

    node_url = os.environ.get("NANO_NODE_URL")
    wallet = os.environ.get("NANO_WALLET_ID")
    if not node_url or not wallet:
        raise SystemExit(
            "NANO_NODE_URL and NANO_WALLET_ID are not set; refusing to start. "
            "This service measures a real node or it measures nothing."
        )
    clock = Clock()
    store = samples.SampleStore(
        clock,
        path=os.environ.get("FINALITY_STORE_PATH"),
        retention_s=int(os.environ.get(
            "FINALITY_RETENTION_S", samples.RETENTION_S)),
    )
    sampler = from_env(RpcNode(node_url, wallet), store, clock)

    compare_sampler = compare_store = None
    compare_url = os.environ.get("FINALITY_COMPARE_NODE_URL")
    if compare_url:
        compare_store = samples.SampleStore(
            clock, path=os.environ.get("FINALITY_COMPARE_STORE_PATH"))
        compare_sampler = from_env(RpcNode(compare_url, wallet),
                                   compare_store, clock)
    base = os.environ.get("FINALITY_PUBLIC_BASE_URL", "http://localhost:8080")
    return Service(sampler, store, clock, base_url=base,
                   compare_sampler=compare_sampler,
                   compare_store=compare_store), [
        s for s in (sampler, compare_sampler) if s]


def main(argv=None):                           # pragma: no cover - entry point
    import argparse

    ap = argparse.ArgumentParser(description="Run the finality proof service.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    service, samplers = build_from_env()
    stop = threading.Event()
    for sampler in samplers:
        threading.Thread(target=sampler.run_forever, args=(stop,),
                         daemon=True).start()
    httpd = serve(service, args.host, args.port)
    print(f"listening on http://{args.host}:{args.port}/v1/health")
    print("Do not quote a figure until the sampler has run for 24 hours.")
    try:
        httpd.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":                     # pragma: no cover
    main()
