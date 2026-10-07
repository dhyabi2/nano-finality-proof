# nano-finality-proof - audit 2026-10-07

Second audit (the first was 2026-10-03). Read through one question: can an agent pay in XNO with
this, today, without being hurt?

HEAD audited: `0149e4b`'s successor on `main` (`git rev-parse HEAD` at the time of the audit is in
the pull request). Python 3.11, standard library only.

## Checked

- **The documented quickstart, exactly as the README prints it.**
  `python3 -m unittest discover -s tests -t tests` **54 passed** before this branch, **59** after;
  `python3 e2e_check.py` **67/67, exit 0**, unchanged. Both counts are pinned by
  `.github/workflows/test.yml`, which parses the figures the README advertises out of the runners'
  own summaries and fails if they drift - so the README's numbers were updated with the tests
  rather than left to be caught by CI.
- **Amount handling.** `amounts.py` is integers only: `parse_raw` refuses a float outright with a
  named reason, refuses a `bool`, refuses a non-digit string and refuses zero or less;
  `format_raw` is `str(int)` and never a float. The sampler sends `DEFAULT_AMOUNT_RAW = 1` - one
  raw - and publishes it as the decimal string of that integer. No float touches an amount
  anywhere in the package. Clean.
- **The percentile arithmetic, which is what the service is for.** `stats.percentile` ranks with
  `Fraction`, so the interpolated rank is exact rather than drifting with binary floating point,
  and rounds through `Decimal` with `ROUND_HALF_UP`. `confirmation_rate` divides in `Decimal` and
  renders a five-place *string*, so two readers cannot disagree about the same rate. Both are
  millisecond-scale figures, well inside the process-global `Decimal` precision of 28 - this is
  not the `prec`-against-raw trap that `nano-mcp-public` and `langchain-vend` both had, because no
  raw amount is ever put through a `Decimal` here.
- **Failures stay in the denominator.** `Sampler.run_once` always records a row, and the timeout
  branch records one with the reason rather than waiting a little longer for a slow sample.
  `confirmation_rate(len(confirmed), len(rows))` is built in the same statement as `elapsed_ms`,
  so there is no path that serves a percentile without its `n`. Matches what the README claims.
- **The timing definition matches the code.** `t0_ms` is taken on the line before `node.send(...)`
  and nowhere else in the package; `elapsed_ms` is closed the first time `confirmed` answers true.
  That is the definition `/v1/finality/method` publishes.
- **No constant stands in for a measurement.** `test_percentiles_are_measured_not_constant` walks
  the package's syntax tree; `FakeNode` deliberately has no default latency. Verified by reading
  the test, not just its name.
- **No test touches the network.** `node.py` is the only module importing `urllib.request`, and
  `test_only_node_py_can_dial_out` pins that by AST. The tests added on this branch construct an
  `RpcNode` but open no socket (the one that drives a sample replaces `_opener`), so that
  invariant and the e2e run's socket behaviour are both unchanged.
- **Secrets in the tree and in history.** None. `NANO_WALLET_ID` and the node URL come from the
  environment; `build_from_env` refuses to start without them and nothing is read from a file.

## Found and fixed

**A node credential could be published on an open endpoint** (`nano_finality/node.py:144`,
`_hostname`), fixed on `fix/node-name-can-publish-a-node-credential`.

`RpcNode.name` was `urlparse(url).hostname or url`. `.hostname` strips a `user:pass@` prefix, but
it is `None` for a URL with **no scheme** - `urlparse("user:key@node.example:7076").hostname` is
`None`, because with no `//` authority everything lands in `path` - and the fallback was the URL
itself.

`name` is not an internal label. It is served as `node` on `/v1/finality`, on
`/v1/finality/method`, on `/v1/finality/compare` and in **every row** of `/v1/finality/samples`,
all of which are open GETs (`service.py:49,71`, `sampler.py:54`, `samples.py:54`). Hosted Nano
nodes are routinely handed out as `https://user:key@node.example`, so an operator who set
`NANO_NODE_URL` without the scheme published their node credential to anyone who asked.

Measured, against the shipped code:

```
RpcNode('user:SUPERSECRETKEY@node.example:7076', 'WALLETID').name
  -> 'user:SUPERSECRETKEY@node.example:7076'
```

and the same string comes back out of `service.finality()["node"]`.

The misconfiguration does not silence the service, which is what makes it worse rather than
self-limiting: `urllib` cannot dial a schemeless URL, so every `send` raises, every sample is
recorded as an **error row** - and an error row carries `node` exactly as a confirmed one does. So
the state in which the credential is published is the state the service settles into.

Fixed by taking the authority by hand when `urlparse` cannot read it: everything after the last
`@` and before the first `/`, `?` or `#`, with a numeric port removed, and `"the configured node"`
rather than an echo of a string nobody vetted. A credential can only appear before an `@`, so
nothing the function can now return still has one. This is the guard `dual-rail`'s `nanonode.host_of`
already carries, with the comment "Node URLs are routinely https://user:key@node.example, and this
string ends up in logs" - the same lesson, reached here by a path that one did not cover.

Five tests added. Four of them fail with `node.py` alone reverted to `main` (8 failures across the
subtests, including the end-to-end one that drives a real sample and asserts the secret appears in
none of `/v1/finality`, `/v1/finality/method` in both renderings, `/v1/finality/samples` or
`/v1/health`). Two of the five are controls that must hold either way: a plain host and an IPv6
literal still read as themselves, so the redaction cannot quietly cost a correct node its name.

## Could not verify

- **No live Nano node was measured.** Every figure in this run comes from `FakeNode` on
  `FakeClock`; this environment's network policy reaches no node, so the published confirmation
  times were not reproduced against mainnet. That is the repository's own standing caveat, not a
  new one.
- **No credential was actually leaked in production.** Whether any deployment of this service has
  ever been configured with a schemeless node URL is not knowable from here. The defect is proven
  in the code and in its published output; the exposure is conditional on that configuration.
- **`RETENTION_S` and the store's on-disk format** were read for the amount path only. Whether the
  30-day retention is right for a finality claim was not assessed, and is a product question
  rather than a defect.
