# nano-finality-proof - audit 2026-10-03

First audit of this repository (published 2026-10-02; `audits/` did not exist). Read through one
question: can an agent rely on this to know when an XNO payment is safe to treat as final,
without being hurt?

HEAD audited: `330a3a8`. Python 3.11, standard library only.

**Nothing was changed. No defect found worth a patch.** This file is the whole of the output.

## Checked

- **Both suites, as the README states them.** `python3 -m unittest discover -s tests`
  **54 passed**; `python3 e2e_check.py` **ALL 67 END-TO-END CHECKS PASSED**. Both counts match
  what `TIER0.md` recorded for this build, so it has not drifted since publication.
- **The send path, which is the one place this service spends.** `RpcNode.send`
  (`node.py:70-82`) passes `amounts.format_raw(amount_raw)` - the exact decimal string of an
  integer - as the node's `amount`, and refuses any answer whose `block` is not 64 hex
  characters. `amounts.parse_raw` refuses a `float`, a `bool`, a non-digit string and anything
  `<= 0`, each with its own message; `format_raw` refuses a non-int. No float touches an amount
  anywhere in the package, and the default sample is `1` raw - the smallest unit there is, so
  the standing exposure of the sampler is 1 raw a minute.
- **The timing definition against the code.** The docstring says `t0` is taken on the line
  before `node.send(...)` and the clock stops the first time the node reports confirmed;
  `sampler.run_once` does exactly that, and `t0_ms` is the only start time taken in the package.
  A competitor measuring from a different point would get a different number, and the service
  publishes which point it used.
- **The percentiles, which are the figure an agent would actually act on.** `stats.percentile`
  computes the rank as a `Fraction` and interpolates in exact rational arithmetic, then rounds
  through `Decimal` with `ROUND_HALF_UP` - so no float enters, and the published definition in
  the module docstring matches the code line for line. It returns `None` for an empty list
  rather than `0`, which would read as a measured zero. `confirmation_rate` divides in `Decimal`
  and renders a fixed five-place string. `elapsed_summary` builds all six figures or all six
  nulls in one statement, so there is no path that serves a percentile without its `n`.
- **Failures stay in the denominator.** `run_once` records a row on every outcome - confirmed,
  timeout and error - and nothing in it looks at the outcome when deciding whether to store.
  The timeout branch carries a comment saying in as many words why a cutoff that waits a little
  longer for a slow sample is how a confirmation rate becomes 100%. That matches the README's
  second rule.
- **Credential exposure through the public endpoints**, which is the thing worth checking in a
  service that holds a node wallet id and serves a "run this yourself" command.
  `NANO_WALLET_ID` is read in `http_app.create_service` and handed to `RpcNode`, and it reaches
  nothing else: `Service._cfg` (`service.py:39-51`) publishes only the source and destination
  accounts, the amount, the intervals, the node's **hostname** and the retention. The accounts
  are public ledger addresses and a stranger needs them to re-check the samples, so publishing
  them is the point. `method.reproduce_command` writes `$WALLET`, `$SRC` and `$DST` as literal
  placeholders inside a single-quoted shell string - they are names for the reader to fill in,
  not values, and they do not expand. **No secret is served, and none is in the tree.**
- **The no-hard-coded-figure rule** the README leads with is enforced by a test that walks the
  package's syntax tree (`test_percentiles_are_measured_not_constant`), and `FakeNode` is
  deliberately given no default latency so a fixture cannot smuggle one in. Read, and the
  mechanism is real rather than asserted.

## Observations - reported, not changed

Neither is a defect; both are places a future change could become one.

- **`Sampler.__init__` does not checksum-validate `source` or `destination`** (`sampler.py:29`),
  and `from_env` only checks that both are non-empty. A typo with a valid checksum would send
  1 raw a minute to the wrong account; one with an invalid checksum is refused by the node and
  recorded as an `ERROR` row, which is visible in `/v1/finality/samples`. The amounts make this
  immaterial today (1 raw = 10**-30 XNO), and it is the operator's own two accounts either way,
  so it is not worth a patch - but `FINALITY_SAMPLE_AMOUNT_RAW` is operator-settable with no
  upper bound, and that is what would make it matter.
- **`run_forever` swallows every non-`NodeError` exception silently** (`sampler.py:98`,
  `except Exception: pass`). The loop outliving one bad sample is right, and `run_once` already
  records a row for anything the node does. But a persistent fault *above* the node layer - a
  store that cannot write, say - would leave the loop spinning with nothing published and no
  error anywhere. It is marked `pragma: no cover` as an entry point, so no test would see it.

## Could not verify

- **No sample was taken against a real Nano node.** Every figure in the suite comes from
  `FakeNode` in virtual time; `test_no_test_touches_the_network` pins that no test opens a
  socket. So the measurement *machinery* is verified and the measurements are not - which is
  the honest position for this repository, since its whole claim is that a reader runs the same
  command against a node we do not control.
- **The retention and compare paths** were read but not exercised beyond what the 67 end-to-end
  checks cover; `/v1/finality/compare` answered `404 not_configured` here, as expected with no
  second node set.
- **Clock behaviour across a monotonic-clock jump** (a suspend, a container migration) was not
  tested. `clock.mono_ms` is the basis of every `elapsed_ms`, and a jump would land in the
  samples as a very large or negative figure; nothing in `stats.py` would reject it.
