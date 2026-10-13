# nano-finality-proof — audit 2026-10-12

Previous audit 2026-10-11. Audited at `3d51946` (`main`). Python 3.11.17 /
3.13, standard library only.

Lens: this service is what tells an agent when an XNO payment is safe to treat
as final. The way it hurts someone is not a crash — it is a figure that reads
better than the node it measured.

Baseline before any change:

```
$ python3 -m unittest discover -s tests     # Ran 59 tests — OK
$ python3 e2e_check.py                      # ALL 67 END-TO-END CHECKS PASSED
$ python3 -m py_compile nano_finality/*.py tests/*.py e2e_check.py   # clean
```

## Found and fixed — a node reply that is not an object dropped the sample

`RpcNode._rpc` (`node.py:67` on `main`) returned whatever `json.loads`
produced, and looked for the node's own `error` field only when that was a
dict:

```python
if isinstance(answer, dict) and answer.get("error"):
    raise NodeError(str(answer["error"]))
return answer
```

So a reply that is **valid JSON but not an object** — `[]`, `null`, a bare
string, a number — passed straight through to `send` and `confirmed`, which
call `.get` on it:

```
$ python3 -I probe_node.py
[]             send         -> !! AttributeError: 'list' object has no attribute 'get'
[]             confirmed    -> !! AttributeError: 'list' object has no attribute 'get'
None           send         -> !! AttributeError: 'NoneType' object has no attribute 'get'
'rate limited' send         -> !! AttributeError: 'str' object has no attribute 'get'
7              confirmed    -> !! AttributeError: 'int' object has no attribute 'get'
```

A real Nano node answers objects. A gateway, reverse proxy or rate limiter in
front of one does not always: a 200 carrying `[]` or a bare string is an
ordinary shape for a hosted RPC endpoint under load, and the module's own
contract is that it never lets one through — `NodeError` is documented as
"The node refused, was unreachable, or answered something unusable."
(`node.py:20`), and it is the one exception `Sampler.run_once` catches and
turns into a row.

**`AttributeError` is not a `NodeError`, so the sample was not recorded at
all:**

```
$ python3 -I probe_sampler.py
rows before   : 0
run_once      : !! AttributeError: 'list' object has no attribute 'get'
rows after    : 0 <- run_once promises 'Always records a row'
ever_sampled  : False
```

That breaks the invariant `run_once` states in its own docstring ("Always
records a row -- confirmed, timeout or error", `sampler.py:44`), and
`run_forever`'s `except Exception: pass` then swallows it silently. The
consequence is the one this package exists not to have: **a dropped sample
never reaches the denominator.** A node answering unusable rubbish read as a
quieter service rather than a failing one, where the same node answering
`{"error": ...}` would have been an error row and pulled the rate down —
`sampler.py:87-90` says it plainly for the timeout case, "a cutoff that quietly
waits a bit longer for a slow sample is how a confirmation rate becomes 100%".

Fixed by refusing a non-object reply in `_rpc`, which is the one place that
reads the wire. It **only adds a refusal**: a reply that used to raise
`AttributeError` out of the package now raises the package's own `NodeError`,
and an object reply — with or without `error` — takes exactly the path it took
before. No amount, destination, rounding or key path is touched.

One visible consequence beyond the sampler: `/v1/health` reads the node
through `reachable()` (`node.py:101-106`, called from `service.py:165`), so a
node whose `version` call answers 200 with a non-object — `[]`, `null`, a
string — used to be reported `"reachable"` and is now reported
`"unreachable"`; `test_health_reads_such_a_node_as_unreachable` pins it.

Five tests added. **Four fail with `node.py` alone reverted to `main`** (13
subtest errors, 4 subtest failures): `send`/`confirmed` raise `AttributeError`
instead of `NodeError`, `run_once` loses the sample instead of recording an
error row, the served `confirmation_rate` holds at `1.00000` over three samples
instead of falling to `0.75000` over four, and `reachable()` reads a
non-object reply as reachable. The fifth is the control that must
hold either way — a normal `{"block": ...}` reply still sends, a
`{"confirmed": "true"}` reply still reads confirmed, and an `{"error": ...}`
reply still refuses.

```
$ python3 -m unittest discover -s tests     # Ran 64 tests — OK
$ python3 e2e_check.py                      # ALL 67 END-TO-END CHECKS PASSED
```

The first push of this branch left the README advertising the old count, and
the repository's own `advertised-counts` job caught it — "unittest suite
measured 63 README says 59 STALE" — while all five Python versions passed.
Corrected here, and moved to 64 when the health test was added. Worth recording that the guard earned its keep against the
run that added the tests, which is exactly the case it exists for.

## Also checked, found clean

- **`stats.percentile` and `_round_half_up`.** The rank `h` is a `Fraction`, so
  it does not drift, and `lo = h.numerator // h.denominator` with the
  `h == lo` early return means `ordered[lo + 1]` is only ever reached when
  `lo < n - 1`: no index can run off the end. `_round_half_up` divides in the
  default 28-digit decimal context, which is the hazard TIER0 names, but the
  operand is a millisecond count of at most a handful of digits and the
  `.5` boundary is only reachable from an exactly-representable fraction, so
  the quantize is exact here. `percentile([])` returns `None`, never `0`.
- **`confirmation_rate`** is a `Decimal` string throughout and returns
  `"0.00000"` for `total <= 0` — the conservative direction.
- **The sampler's clock discipline.** `t0_ms` is taken on the line before
  `node.send` and nowhere else in the package, and `elapsed_ms` is
  `now_ms - t0_ms` where `now_ms` is read *before* the `is_confirmed` branch,
  so a slow poll cannot be charged to the network or vice versa. A timeout
  stays in the denominator (`sampler.py:86-93`) and `prune` never looks at
  `outcome`.
- **`Service.compare`'s `not self.compare_store` guard.** `SampleStore`
  defines neither `__len__` nor `__bool__`, so a configured-but-empty second
  store is truthy and reports its real state (503 `no_samples_yet`) rather
  than reading as "not configured". Worth pinning if a `__len__` is ever
  added; it is not a defect today.
- **`_hostname`** cannot return a string containing an `@`, so no node
  credential can reach the published `node` label, by any of the URL shapes
  the existing test matrix covers.
- **Amounts.** `amounts.py` is integer raw throughout and `format_raw` puts a
  decimal string on the wire. No float touches an amount anywhere in the
  package. The three `float(...)` calls that exist are on an interval, a
  timeout and an epoch second — time, not money.
- **README URLs** all resolve; the quickstart commands are the three run above.

## What I could not verify

- Anything against a live Nano node. `test_only_node_py_can_dial_out` pins
  that the suite never opens a socket, and this audit kept that property: the
  new tests inject an opener rather than dial out. So the claim that a hosted
  RPC gateway really does answer `[]` under load is reasoning from the shape
  of such services, not a captured reply — what is *measured* here is only
  what this package does when one arrives.
- Whether a deployed instance of this service is serving the fixed code. A fix
  merged is not a fix served, and a cloud run cannot read the box.
