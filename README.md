# nano-finality-proof

**"Zero fees means no economic finality signal."** That objection is correct, and
this service does not argue with it.

On a fee market the fee a sender pays is public evidence of how much work an
attacker has to outbid to reverse a transaction. Nano has no fee, so that
particular signal is genuinely absent. Nothing here claims otherwise.

What this service does instead is measure the thing you actually wanted to know:
**how long until my payment is irreversible, and how often is that wrong?** It
sends itself one minimal payment on a fixed interval, times it, and publishes the
numbers — with the sample size, the window, the node, the failures, and the
method, so you can run the same measurement against a node we do not control and
compare.

Every figure this service serves is measured. There is no constant in the source
that is a confirmation time, and there is a test that fails if one appears.

```
GET /v1/finality          the summary, for a window
GET /v1/finality/samples  every raw row, including the failures
GET /v1/finality/method   how it was measured, ending in a command you can run
GET /v1/finality/compare  the same measurement against a second node
GET /v1/health
```

## The four rules this is built on

1. **Every figure is measured, never asserted.** `test_percentiles_are_measured_not_constant`
   walks the package's syntax tree and fails on any numeric literal in the range
   a confirmation time occupies that is not named, with a reason, in the test
   itself.
2. **Failures are published.** A timeout is stored exactly like a confirmation,
   is served in the raw rows, and stays in the denominator of
   `confirmation_rate`. Nothing in the code looks at a sample's outcome when
   deciding whether to keep it.
3. **A percentile never appears without its `n`.** `samples`, `confirmed`,
   `timeouts`, `errors` and `confirmation_rate` are built in the same statement
   as `elapsed_ms`; there is no code path that emits one without the others.
4. **The method ships with the numbers**, generated from the sampler's own
   configuration, so it cannot drift from what is running.

## Run it

Python 3.9+. No dependencies — standard library only.

```bash
git clone https://github.com/dhyabi2/nano-finality-proof && cd nano-finality-proof
python3 -m unittest discover -s tests -t tests   # 59 tests
python3 e2e_check.py                             # 67 checks over a real socket
```

### A runnable example — no Nano node needed

Save this as `example.py` in the repository root and run `python3 example.py`.
It uses the in-package fake node on virtual time, so it finishes instantly and
touches no network.

```python
from nano_finality.clock import FakeClock
from nano_finality.node import FakeNode
from nano_finality.sampler import Sampler
from nano_finality.samples import SampleStore
from nano_finality.service import Service

clock = FakeClock()
node = FakeNode(clock, latency_ms=412, name="rpc.example-node.org")
store = SampleStore(clock)
sampler = Sampler(node, store, clock, "nano_1source...", "nano_1dest...")
service = Service(sampler, store, clock, base_url="https://finality.example.org")

# 200 samples a minute apart, and two that never confirm.
for i in range(200):
    sampler.run_once()
    clock.advance(60)
node.latency_ms = 99_999          # past the 30s cutoff
for i in range(2):
    sampler.run_once()
    clock.advance(60)

summary = service.finality("24h")
print(summary["samples"], "samples,", summary["confirmed"], "confirmed,",
      summary["timeouts"], "timed out")
print("confirmation_rate:", summary["confirmation_rate"])
print("elapsed_ms:", summary["elapsed_ms"])
print(summary["honest_note"])
```

```
202 samples, 200 confirmed, 2 timed out
confirmation_rate: 0.99010
elapsed_ms: {'p50': 450, 'p90': 450, 'p95': 450, 'p99': 450, 'max': 450, 'min': 450}
Nano has no fee, so it has no fee-based finality signal. It does not have one and
we do not claim it does. What is measured here is time to confirmation and how
often confirmation failed, against the node named above, which you can query
yourself.
```

Two things in that output are worth stopping on.

**`202`, not `200`.** The two timeouts are in the denominator. That is the whole
point.

**`450`, when the node confirmed in `412`.** Confirmation is detected by polling
every `FINALITY_POLL_INTERVAL_MS` (50 by default), so a measured `elapsed_ms`
can overstate the true one by up to one poll interval, and never understates it.
That bias is real, it is in every figure this service publishes, and it is stated
in `/v1/finality/method` rather than quietly corrected for. An operator who wants
finer resolution lowers the poll interval and pays for it in RPC load; an
operator who "corrects" the figure downward by half an interval is inventing
data. We do not.

### Run it for real

```bash
export NANO_NODE_URL=http://[::1]:7076
export NANO_WALLET_ID=...                 # your node's wallet id
export FINALITY_SOURCE_ACCOUNT=nano_...   # an account you fund
export FINALITY_DEST_ACCOUNT=nano_...     # a second account you control
export FINALITY_STORE_PATH=/var/lib/finality/samples.jsonl
python3 -m nano_finality.http_app --host 0.0.0.0 --port 8080
```

The service refuses to start without a node URL and a wallet id. **No key, seed
or node credential is in any file in this repository**, and none is ever read
from one: everything comes from the environment.

| variable | default | what it is |
| --- | --- | --- |
| `NANO_NODE_URL` | — | required; the node the sampler measures |
| `NANO_WALLET_ID` | — | required; the wallet holding the source account |
| `FINALITY_SOURCE_ACCOUNT` | — | required; the funded account that pays |
| `FINALITY_DEST_ACCOUNT` | — | required; the second account that receives |
| `FINALITY_SAMPLE_INTERVAL_S` | `60` | seconds between samples |
| `FINALITY_SAMPLE_TIMEOUT_MS` | `30000` | the cutoff; past it, a sample is a timeout |
| `FINALITY_POLL_INTERVAL_MS` | `50` | how often confirmation is checked |
| `FINALITY_SAMPLE_AMOUNT_RAW` | `1` | one raw, the smallest unit there is |
| `FINALITY_RETENTION_S` | `2592000` | 30 days |
| `FINALITY_STORE_PATH` | in memory | JSON Lines; survives a restart |
| `FINALITY_COMPARE_NODE_URL` | unset | a second, independently operated node |
| `FINALITY_PUBLIC_BASE_URL` | `http://localhost:8080` | what goes in the published URLs |

**Do not quote a figure from this service until the sampler has run for at least
24 hours.** A twenty-minute window presented as a finality figure is the
assertion problem again, wearing a number.

## How to check us

The summary is only worth as much as the rows behind it, so the rows are public:

```bash
curl -s https://<host>/v1/finality/samples?window=24h | \
  python3 -c 'import json,sys; [print(r["block_hash"], r["elapsed_ms"], r["outcome"]) for r in json.load(sys.stdin)["samples"]]'
```

Take any hundred of those hashes to a node we do not run and look them up. Then
read `/v1/finality/method` and run the command at the end of it against your own
node — the timing definition is stated there precisely enough that your number
and ours are comparable:

> `elapsed_ms` is measured from the moment the send RPC is **dispatched** to the
> moment the node **first reports the block confirmed**. Not from block
> construction, not from local signing, not from the moment the send RPC returns.

If your number disagrees with ours, the method is where to look for why.

## What is in the money path

One raw. `1 XNO = 10**30 raw`, which is more significant digits than a float
holds, so the amount is carried as an `int` and rendered with `str()` — see
`nano_finality/amounts.py`, which refuses a float outright rather than rounding
one.

## The two numbers people get wrong

- **`confirmation_rate` is a decimal string, not a float.** `1434/1436` is
  `"0.99861"`. A float would serve `0.9986072423398329` and two readers would
  round it differently.
- **Percentiles are computed over confirmed samples only; the rate is over all
  of them.** Both are always shown together. A p50 with no rate beside it hides
  exactly the samples that make the p50 look good.

## Who made this, and its honest limits

Written and maintained by **dhyabi2**, as part of an effort to make Nano (XNO)
a practical payment rail for AI agents. It is ours, not an official Nano
Foundation project.

- **We do not publish figures yet.** This repository is the measuring
  instrument, not a measurement: no public instance of it is running, and no
  confirmation-time number appears anywhere in this repository. The figures in
  the example above come from the fake node on virtual time.
- **Running it for real spends money and needs a node with a wallet.** Each
  sample sends 1 raw from an account you fund to a second account you control,
  through your node's `send` RPC. That needs a node you operate (public RPC
  proxies do not expose wallet actions). One sample a minute is 1,440 raw a day,
  which is a negligible amount, but it is a real send.
- **It measures one node's view.** Time-to-confirmation as reported by the node
  you point it at; `/v1/finality/compare` adds a second node, it does not make
  the figure network-wide.
- **Polling bias**: figures can overstate by up to one poll interval (see above).
- Version 1.0.0; not on PyPI.

## Built from

A written spec with 12 numbered acceptance tests.
All 12 of that spec's numbered tests are implemented, named after it, and run in
`tests/test_acceptance.py` and again over HTTP in `e2e_check.py`.

MIT licensed.
