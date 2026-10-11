# nano-finality-proof — audit 2026-10-11

Previous audit 2026-10-09. Read through one question: can an agent pay — or get paid — in XNO with
this, today, without being hurt?

HEAD audited: `0c0e0e7`. Python 3.11.17, standard library only.

**Nothing worth a patch.** This file is the whole result; no production line is touched. The three
observations the 2026-10-09 audit recorded are unchanged and still not defects; one new observation
is below, and it is of the same tier.

Baseline, exactly as the README prints it:

| README says | measured |
| --- | --- |
| `python3 -m unittest discover -s tests -t tests` — 59 tests | **Ran 59 tests — OK** |
| `python3 e2e_check.py` — 67 checks over a real socket | **ALL 67 END-TO-END CHECKS PASSED**, exit 0 |

Both match. The README states its own counts, so it goes stale silently if a suite grows; it has not.

## Found — one new observation, not a defect in what ships

**`amounts.parse_raw` screens a raw string with `str.isdigit()`** (`nano_finality/amounts.py:24`),
which is true for every Unicode digit, not just ASCII:

```
isdigit on Arabic-Indic 3 : True -> int('٣') = 3
isdigit on superscript 2 : True
   int('²') raises ValueError: invalid literal for int() with base 10: '²'
```

So `FINALITY_SAMPLE_AMOUNT_RAW="٣"` is silently read as 3 raw, and `"²"` passes the screen and then
raises a bare `ValueError` out of `parse_raw` — past the `AmountError` contract the module's
docstring states and past every caller written to expect one.

**Why it is not fixed here.** The only caller is `Sampler.__init__` (`sampler.py:39`), and its
argument comes from `FINALITY_SAMPLE_AMOUNT_RAW` — the **operator's** environment, never a payer's
input. `grep -rn "parse_raw"` finds no other call site. Both outcomes are therefore a
misconfiguration at startup, where the operator sees a traceback either way: `AmountError` is itself
an uncaught exception at that point, so the fix would change the wording of a crash and nothing else.
No amount a payer can influence passes through this function, and the sampled amount is 1 raw
(10⁻³⁰ XNO) by default. Recorded so a later run need not re-derive it; a patch would be noise.

This is worth noting only because it is the **same function, the same screen and the same two
counter-examples** that the sibling repository `dual-rail` found reachable and fixed in its own
`money.py` (its comment at `money.py:49-54` records "٣" reading as 3 XNO in Python while the Node
binding refused it). There, the amount was a **price in a published 402 challenge** and there was a
second binding to stay conformant with. Here there is neither, which is the whole of the difference.

## Checked and clean

- **No float anywhere on an amount.** `amounts.py` refuses a `float` and a `bool` outright in both
  directions, parses a digit string to `int`, and `format_raw` is `str(raw)` — exact, no rounding.
  `RpcNode.send` puts `amounts.format_raw(amount_raw)` on the wire, so the amount crosses as the
  decimal string of an integer raw count, which is what a node expects.
- **`Decimal` is used, and its process-global precision cannot reach a figure here.** `stats.py`
  imports `Decimal` for `_round_half_up` and `confirmation_rate`, which is the pattern that caused
  a real under-receive in `nano-mcp-public` (a 28-digit default context truncating a 31-digit raw
  amount). It cannot bite here: neither call site touches a raw amount. `_round_half_up` divides a
  `Fraction` whose quotient is a **millisecond** count, and `confirmation_rate` divides two sample
  counts and quantizes to five places — both far inside 28 significant digits. The rank itself is a
  `Fraction`, so the percentile position is exact and does not drift.
- **`confirmed` is normalised, not read for truthiness.** `RpcNode.confirmed` (`node.py:87`) is
  `str(answer.get("confirmed", "false")).lower() == "true"`, so a node answering the *string*
  `"false"` — truthy in Python — is correctly read as unconfirmed, and a missing field defaults to
  unconfirmed. `send` refuses an answer whose `block` is not 64 hex characters rather than timing a
  hash it cannot use.
- **A node credential cannot reach a published field.** `node.name` is served as `node` on four open
  GETs including every error row, and `_hostname` (`node.py:144-177`) takes the authority by hand
  when `urlparse` returns no hostname — which is the case for a scheme-less
  `user:key@node.example:7076` — cutting at the first `/?#` and keeping only what follows the last
  `@`. Nothing it can return still holds an `@`.
- **No shell, no eval, no path sink from a request.** `grep -rnE "os.system|subprocess|shell=True|
  eval\(|exec\(|pickle|__import__"` over `nano_finality/` finds nothing. The only two `open()` calls
  (`samples.py:86`, `:105`) take `FINALITY_STORE_PATH` from the operator's environment; no part of a
  URL, query string or header reaches a filesystem path.
- **The HTTP surface accepts only what it names.** `do_GET` dispatches on a regex match against the
  parsed path and serves `404 not_found` otherwise; `GET` is the only verb routed. Every query
  parameter is read with a default (`window`, `limit`, `cursor`), and a non-integer `limit` is
  answered `400 invalid_limit` rather than raised — `int('²')` lands there and is handled. There is
  no write endpoint, so nothing a stranger submits can enter the sample set or move an amount.
- **No committed secret, tree or history.** Five commits, no path matching `*.key`, `*.pem`, `.env`,
  `seed`, `secret` or `credential`. The only occurrence of the word in the tree is `README.md:125`
  stating that no key or seed is stored, which is true of what was read.

## Could not verify

- **Anything against a real Nano node.** This environment's network policy blocks the hosts
  involved, so `send`, `confirmed` and `reachable` are exercised only against `FakeNode` and the
  recorded reply shapes in the tests. Unchanged from the previous two audits, and the suite is built
  not to need one.
- **Only Python 3.11.17 was run.** The README's floor is 3.9; 3.9, 3.10, 3.12 and 3.13 are unproven
  from here.
- **That no public instance is running**, and so that no figure served anywhere is stale. The README
  says there is none; that is a statement about the world, not about the tree.
