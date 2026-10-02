"""Shared fixtures. Every test runs on virtual time against a fake node."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nano_finality.clock import FakeClock                      # noqa: E402
from nano_finality.node import FakeNode                        # noqa: E402
from nano_finality.sampler import Sampler                      # noqa: E402
from nano_finality.samples import SampleStore                  # noqa: E402
from nano_finality.service import Service                      # noqa: E402

SOURCE = "nano_3t6k35gijtsz6mokg push_source_placeholder"
DEST = "nano_1dest_placeholder"
BASE_URL = "https://finality.example.org"


def build(latency_ms=400, clock=None, node_name="rpc.example-node.org",
          store_path=None, **sampler_kw):
    """A service, its sampler and its fake node, all on one virtual clock."""
    clock = clock or FakeClock()
    node = FakeNode(clock, latency_ms=latency_ms, name=node_name)
    store = SampleStore(clock, path=store_path)
    sampler = Sampler(node, store, clock, SOURCE, DEST, **sampler_kw)
    service = Service(sampler, store, clock, base_url=BASE_URL)
    return service, sampler, node, store, clock


def take(sampler, n, gap_s=60):
    """n samples, `gap_s` apart, the way the real sampler spaces them."""
    for _ in range(n):
        sampler.run_once()
        sampler.clock.advance(gap_s)
