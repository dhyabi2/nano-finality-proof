# nano-finality-proof — audit 2026-10-14

Previous audit 2026-10-12. Audited at `e2243b5` (`main`, "A node reply that is not an
object is a node error, not a lost sample (#6)"). Python 3.11, standard library only.

Lens: this service is what tells an agent when an XNO payment is safe to treat as final.
The way it hurts someone is not a crash — it is a figure that reads better, or cleaner,
than the node it measured.

Baseline before any change:

```
$ python3 -m unittest discover -s tests     # Ran 64 tests — OK
$ python3 e2e_check.py                      # ALL 67 END-TO-END CHECKS PASSED
$ python3 -m py_compile nano_finality/*.py tests/*.py e2e_check.py   # clean
```

With this branch: **70 tests — OK**, 67 end-to-end checks, compile clean.

Every prior audit read the measuring code. This run read the **wiring** — the one function
no test had ever constructed — and compared the second node's configuration, knob by knob,
against the first's.

## Checked

- **No float on an amount.** `amounts.parse_raw` refuses a float outright and refuses a
  bool before it (`bool` is an `int`); `format_raw` is `str(int)`. The sampler carries an
  integer count of raw and publishes its exact decimal string. Unchanged.
- **Durations are measured on the monotonic clock, never on `now()`** (`clock.py:18`), and
  `t0_ms` is taken on the line before the dispatch (`sampler.py:69`) — the start point the
  published method claims. A block that confirms exactly at the deadline is counted
  confirmed, not timed out (`sampler.py:88` runs before the deadline test). Unchanged.
- **Nothing drops a sample.** `run_once` records a row on every path: a `NodeError` out of
  `send`, a `NodeError` mid-poll, a timeout, a confirmation. `prune` does not look at
  `outcome`, and `confirmation_rate`'s denominator is every row in the window. Unchanged.
- **A percentile never escapes without its `n`:** `elapsed_summary` returns all six figures
  or six nulls, and `finality` builds `samples`/`confirmed`/`timeouts`/`errors`/
  `confirmation_rate` in the same statement as `elapsed_ms`. `percentile([])` is None and
  never 0. Unchanged.
- **`503 no_samples_yet` is still checked before the window** (`service.py:72`), so an
  outage cannot read as a quiet hour.
- **The published node name still carries no credential**, for every shape of
  `NANO_NODE_URL` including a schemeless `user:key@host:7076`. Unchanged; re-run.
- **Nothing but `node.py` can dial out** — the AST test still reports exactly one importer,
  with the new test class added.
- **No confirmation-time constant appeared in the package.** The syntax-tree test is green
  and this branch adds no numeric literal to `nano_finality/`.

## Found and fixed — the second column of /v1/finality/compare kept a different span of history

`build_from_env` read `FINALITY_RETENTION_S` for the primary store and gave the compare
store nothing (`http_app.py:112,121` on `main`):

```python
    store = samples.SampleStore(
        clock,
        path=os.environ.get("FINALITY_STORE_PATH"),
        retention_s=int(os.environ.get(
            "FINALITY_RETENTION_S", samples.RETENTION_S)),
    )
    ...
        compare_store = samples.SampleStore(
            clock, path=os.environ.get("FINALITY_COMPARE_STORE_PATH"))
```

So the compare store always pruned at the 30-day default, whatever the operator set. Every
*other* knob the second sampler takes already came from the same environment through
`from_env` — interval, timeout, poll interval, amount, source, destination — which is what
makes this one the exception rather than a design choice.

Two published things stop being true as soon as the two differ. `/v1/finality/compare`
serves the same window computed over each store under its own note:

> The same measurement, run against two independently operated nodes.

and `/v1/finality/method` publishes ONE `retention_days`, read off the primary store.

Measured, with `FINALITY_RETENTION_S` set to 7 days and 20 days of hourly sampling against
two fake nodes that answer *equally often* — node B is only slower:

```
$ python3 -I demo_retention.py
node-a.example   samples=  168  p50= 400  rate=1.00000
node-b.example   samples=  480  p50= 900  rate=1.00000
published retention_days on /v1/finality/method: 7
note: The same measurement, run against two independently operated nodes.
```

168 against 480 for the same 30-day question, because A's rows were pruned at 7 days and
B's at 30. Nothing about either node differs; only how much of its history survived. A
reader comparing the two columns — which is the entire purpose of the endpoint, and the
reason the note refuses to average them — is comparing a week against a month, told it is
the same measurement, under a method document that says seven days for both.

It also runs the wrong way round from everything else in this service: the
honest-measurement rules here all narrow what is claimed, and this quietly widened one
column's sample count relative to the other. A slow day inside B's extra 23 days of
history is a figure A's column cannot show, in either direction.

The fix reads the retention window once and gives it to both stores. `build_from_env` lost
its `# pragma: no cover - entry point`, because it is now driven by a test — it constructs
`RpcNode` and never calls it, which opens no socket, the same thing the credential tests
above it already do.

```
$ python3 -m unittest discover -s tests -k BothStoresShareOneRetentionWindow
  AssertionError: 2592000 != 604800     # the compare store, with 7 days asked for
  AssertionError: 2592000 != 7776000    # and with 90
  AssertionError: 30 != 7               # the published retention_days describes one column
  Ran 6 tests — FAILED (failures=3)
$ ... with the fix
  Ran 70 tests — OK                     # was 64
```

Three of the six are new assertions that fail on `main`. The other three pin what was
already right and pass either way: the 30-day default on both stores, the other six
sampler knobs already matching, and no compare node meaning no second store at all.

## Could not verify

- **No live node was measured.** Every check is offline, against `FakeNode` on a
  `FakeClock`. The figures in the demonstration above are virtual-time, which is the point
  — they isolate retention from node behaviour — but no real `rpc.*` endpoint was dialled.
- **`/v1/health` describes the primary only** (`service.py:160`): `sampler_running`,
  `last_sample_at` and `samples_24h` come from `self.sampler` and `self.store`, and
  `node` reports the primary's reachability. So when it is the *compare* store that has
  never recorded a sample, `/v1/finality/compare` answers 503 `no_samples_yet` — whose
  message says "check GET /v1/health for sampler_running and node reachability" — and
  `/v1/health` answers `ok: true` with everything green. A second concern, left for its own
  branch: it changes a published surface and deserves a decision about what a two-node
  health body should look like rather than a guess.
- **`from_env` crashes with a bare traceback on a malformed knob.** A non-numeric
  `FINALITY_SAMPLE_INTERVAL_S`, or a `FINALITY_SAMPLE_AMOUNT_RAW` that is not integer raw,
  raises `ValueError`/`AmountError` out of startup rather than the `SystemExit` with a
  sentence that a missing account gets (`sampler.py:116`). It fails closed, so nothing is
  mismeasured; it is only unkind. Not touched — it is cosmetics on an entry point, and this
  branch is about a figure.
