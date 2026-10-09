# nano-finality-proof - audit 2026-10-09

Third audit (2026-10-03, 2026-10-07 before it). Read through one question: can an agent pay in XNO
with this, today, without being hurt?

HEAD audited: `c0f6a00` on `main`. Python 3.11.17, standard library only.

**Nothing was changed.** No defect was found that is worth a patch, so this audit file is the whole
of the change. What follows is what was actually run and read, and the four checks that are new
since 2026-10-07 - the earlier audits' ground is not re-asserted here, it was re-run.

## Checked

- **Both documented commands, exactly as the README prints them.**
  `python3 -m unittest discover -s tests -t tests` -> **59 tests, OK**.
  `python3 e2e_check.py` -> **ALL 67 END-TO-END CHECKS PASSED**. Both match the counts the README
  advertises.
- **NEW: the README's runnable example, run as a reader would run it.** The `python3` block under
  "A runnable example" was extracted from README.md verbatim, written to `example.py` in the
  repository root as the text instructs, and run. Its output is byte-identical to the output the
  README prints beside it, including `202 samples, 200 confirmed, 2 timed out`,
  `confirmation_rate: 0.99010` and all six `elapsed_ms` figures at `450`. The two facts the
  README then stops on - 202 rather than 200, and 450 against the fake node's 412 - both hold in
  the real output. The file was deleted again; it is not part of this change.
- **NEW: the README's "Python 3.9+" claim.** Searched the package for syntax that would not parse
  on 3.9 (`match`/`case`, `X | None` annotations): none. The suite was then run on **3.12.x
  (59 tests, OK)** and **3.13.x (59 tests, OK)** as well as 3.11. 3.9 and 3.10 interpreters are not
  present in this environment. **The floor itself is nevertheless proven, by CI rather than by this
  run:** `.github/workflows/test.yml:19` runs the matrix `["3.9","3.10","3.11","3.12","3.13"]`, and
  all five jobs are green on the pull request carrying this file - so the README's "Python 3.9+" is
  a supported claim and not just a consistent one.
- **NEW: the credential-stripping in the published node label.** `node._hostname` is the one
  function standing between an operator's `https://user:key@node.example` and an open GET, because
  `self.name` is served as `node` on four public endpoints including every error row. Driven
  directly over ten URL shapes: a `user:pass@` prefix with a scheme, without a scheme, with a
  port, with a query, with IPv6, bare and empty. Every answer is the host alone; no returned string
  contains an `@` or any part of a credential. The no-scheme case the docstring was written for
  (`user:key@node.example:7076` -> `node.example`) is correct, and the IPv6 cases return the
  address with the brackets stripped by `urlparse`, which is the host and not a credential.
- **Amount handling, re-run rather than inherited.** `amounts.parse_raw` refuses a float, a bool,
  a non-digit string and zero-or-less, each with a named reason; `format_raw` is `str(int)`.
  `grep` over the package finds no `float(` on an amount. The sampler sends
  `DEFAULT_AMOUNT_RAW = 1` and publishes it as that integer's decimal string.
- **The `Decimal` precision trap that hit four sibling repositories is not reachable here.**
  `stats._round_half_up` and `confirmation_rate` are the only `Decimal` divisions in the
  package and both operate on millisecond-scale and count-scale integers - five or six significant
  digits against a process-global `prec` of 28. No raw amount is ever put through a `Decimal`,
  which is what makes this repository's arithmetic immune to the defect `nano-mcp-public`,
  `langchain-vend` (#5), `gpt-researcher-x402-retriever` (#8) and
  `openai-agents-nano-x402` (#20) each carried.
- **The send path.** `RpcNode.send` crosses the wire with `amounts.format_raw(amount_raw)` - a
  decimal string of an integer - and refuses any answer whose `block` is not 64 hex characters.
  `sampler.run_once` pairs no two reads of node state: it dispatches, then polls one hash. There
  is no frontier/balance pair here to get wrong, so the class of defect open as
  `nano-wallet-xno#4` has no analogue in this repository.
- **Input reaching the HTTP surface.** `limit` is `int()` inside a `try` and then range-checked
  in `service.samples_page` against `LIMIT_MIN`/`LIMIT_MAX` with an explicit `isinstance`
  and a `bool` exclusion; `window` is a dictionary lookup that 400s on anything unknown;
  `cursor` is base64 that must decode to a `smp_` id present in the window. All five refusals are
  exercised by the e2e run over a real socket. No path reaches a shell, a file path or a format
  string from the query.
- **Secrets, in the tree and in `git log --all -p`.** None. `NANO_NODE_URL` and
  `NANO_WALLET_ID` come from the environment and `build_from_env` refuses to start without them.

## Found

Nothing worth a patch. Three observations are recorded because a later run should not have to
re-derive them, and none of them hurts a payer today:

1. **The JSON Lines store is never compacted.** `SampleStore.prune` drops rows past retention from
   memory, but `_append` only ever appends and `_load` reads every line back without pruning. The
   file therefore grows without bound, and between a restart and the first new sample the store
   holds rows older than retention. No served figure is affected: the widest window (`30d`) equals
   the default retention, and every read goes through `in_window`. An operations note, not a
   defect.
2. **The compare store does not inherit `FINALITY_RETENTION_S`.** `http_app.build_from_env`
   passes `retention_s` to the primary `SampleStore` and not to the compare one, so an operator
   who shortens retention gets it on one column of `/v1/finality/compare` and the 30-day default
   on the other. Both columns still carry their own `retention_s` in `/v1/finality/method`, so
   the asymmetry is published rather than hidden.
3. **A second node doubles the real sends.** With `FINALITY_COMPARE_NODE_URL` set, two samplers
   run on the same source and destination accounts, so the README's "1,440 raw a day" is 2,880. The
   amount is negligible by construction; the README's arithmetic is for the single-node case it
   describes.

## Could not verify

- **Anything against a real Nano node.** This environment's network policy answers 403 to CONNECT
  for the hosts involved, and the suite is built not to need one. No `RpcNode` was constructed
  against a live RPC, so `send`/`confirmed`/`reachable` are verified only against the
  recorded shapes in the tests.
- **That no public instance is running.** The README says there is none and that no figure in the
  repository is a measurement; that is consistent with everything read here, but it is a statement
  about the world, not about the tree.
