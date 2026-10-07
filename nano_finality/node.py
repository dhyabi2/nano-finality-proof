"""The node behind one interface, so no test needs a live Nano node.

Two methods and nothing else. `send` dispatches the minimal self-payment and
returns the block hash; `confirmed` answers whether the node reports that
block confirmed yet. The sampler times the gap between the two, and knows
nothing about how either is implemented.

`RpcNode` is the only class in this package that opens a socket, and the suite
never constructs one -- `test_no_test_touches_the_network` pins that.
"""

import hashlib
import json
import urllib.request

from . import amounts


class NodeError(RuntimeError):
    """The node refused, was unreachable, or answered something unusable."""


class NanoNode:
    """The interface the sampler is written against."""

    name = "unconfigured"

    def send(self, source: str, destination: str, amount_raw: int) -> str:
        raise NotImplementedError

    def confirmed(self, block_hash: str) -> bool:
        raise NotImplementedError

    def reachable(self) -> bool:
        raise NotImplementedError


class RpcNode(NanoNode):
    """A real Nano node over its JSON-RPC.

    The wallet id and account are the operator's and come from the
    environment; nothing here is stored in the repository. Amounts cross the
    wire as decimal *strings* of an integer raw count, which is what the node
    expects and what keeps a float out of the money path.
    """

    def __init__(self, url: str, wallet: str, timeout_s: float = 10.0,
                 opener=None):
        self.url = url
        self.wallet = wallet
        self.timeout_s = timeout_s
        self.name = _hostname(url)
        self._opener = opener or urllib.request.urlopen

    def _rpc(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json",
                     "User-Agent": "nano-finality-proof/1.0"},
        )
        try:
            with self._opener(req, timeout=self.timeout_s) as resp:
                answer = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:                     # noqa: BLE001 - any failure is a node failure
            raise NodeError(f"{type(exc).__name__}: {exc}") from None
        if isinstance(answer, dict) and answer.get("error"):
            raise NodeError(str(answer["error"]))
        return answer

    def send(self, source, destination, amount_raw):
        answer = self._rpc({
            "action": "send",
            "wallet": self.wallet,
            "source": source,
            "destination": destination,
            "amount": amounts.format_raw(amount_raw),
        })
        block = answer.get("block")
        if not _is_block_hash(block):
            raise NodeError(f"send returned no usable block hash: {answer!r}")
        return block.upper()

    def confirmed(self, block_hash):
        answer = self._rpc({"action": "block_info", "json_block": "true",
                            "hash": block_hash})
        return str(answer.get("confirmed", "false")).lower() == "true"

    def reachable(self):
        try:
            self._rpc({"action": "version"})
            return True
        except NodeError:
            return False


class FakeNode(NanoNode):
    """A node whose confirmation latency the caller sets, in virtual time.

    `latency_ms` may be a number or a callable taking the sample index, so a
    test can change the latency partway through a run -- which is what proves
    the published percentile is measured and not baked in. It has no default
    on purpose: a fake node with a built-in confirmation time is exactly the
    hard-coded figure the spec's first non-negotiable forbids, even in a
    fixture, and `test_no_confirmation_time_constant_exists_in_the_source`
    would find it.
    """

    name = "fake-node.invalid"

    def __init__(self, clock, latency_ms, name=None, fail_with=None):
        self.clock = clock
        self.latency_ms = latency_ms
        self.fail_with = fail_with
        self._sent = {}
        self._n = 0
        self.up = True
        if name:
            self.name = name

    def _latency_for(self, index):
        if callable(self.latency_ms):
            return self.latency_ms(index)
        return self.latency_ms

    def send(self, source, destination, amount_raw):
        if self.fail_with:
            raise NodeError(self.fail_with)
        index = self._n
        self._n += 1
        block = hashlib.blake2b(f"fake-block-{index}".encode(),
                                digest_size=32).hexdigest().upper()
        self._sent[block] = self.clock.mono_ms() + self._latency_for(index)
        return block

    def confirmed(self, block_hash):
        due_ms = self._sent.get(block_hash)
        return due_ms is not None and self.clock.mono_ms() >= due_ms

    def reachable(self):
        return self.up


def _hostname(url: str) -> str:
    """The node's host, and never anything that could be a credential.

    `self.name` is a label, and it is a PUBLISHED one: it is served as `node`
    on `/v1/finality`, on `/v1/finality/method`, on `/v1/finality/compare` and
    in every row of `/v1/finality/samples`, all of which are open GETs.

    `urlparse(...).hostname` strips a `user:pass@` prefix, but it returns None
    for a URL with no scheme - `urlparse("user:key@node.example:7076").hostname`
    is None, because everything lands in `path` - and the fallback was the URL
    itself. Hosted Nano nodes are routinely handed out as
    `https://user:key@node.example`, so an operator who set NANO_NODE_URL
    without the scheme published their node credential on an open endpoint.
    (The RPC itself cannot work without a scheme, so every sample is an error
    row - and an error row carries `node` too.)

    So the authority is taken by hand when urlparse cannot: everything after
    the last `@` and before the first `/`, `?` or `#`, with a numeric port
    removed. A credential can only appear before an `@`, and nothing here can
    return a string that still has one.
    """
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname
    if hostname:
        return hostname
    authority = url.split("://", 1)[-1]
    for cut in ("/", "?", "#"):
        authority = authority.split(cut, 1)[0]
    authority = authority.rsplit("@", 1)[-1]
    head, sep, tail = authority.rpartition(":")
    if sep and tail.isdigit() and "]" not in authority:   # a port, not IPv6
        authority = head
    return authority or "the configured node"


def _is_block_hash(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdefABCDEF" for c in value)
    )


is_block_hash = _is_block_hash
