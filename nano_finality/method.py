"""The published method.

This text is the difference between a measurement and an assertion, so it is
generated from the service's own configuration rather than written out by
hand: the interval, the timeout and the node named below are the ones the
sampler is actually using. A method document that could drift from the code
is a method document nobody should believe.
"""

from . import samples
from .stats import RATE_DP, RANKS

TIMING_START = "dispatched"
TIMING_STOP = "first reports the block confirmed"


def method_json(cfg: dict) -> dict:
    return {
        "what_is_measured": (
            "Time from dispatching a Nano send RPC to the node first "
            "reporting that block confirmed, and how often that never "
            "happened inside the timeout."
        ),
        "what_is_not_measured": (
            "Nano has no fee, so there is no fee-based finality signal to "
            "measure and this service does not report one."
        ),
        "payment": {
            "from": cfg["source"],
            "to": cfg["destination"],
            "amount_raw": cfg["amount_raw"],
            "note": (
                "A self-payment between two accounts we control, so no third "
                "party is involved in the timing. The amount is an integer "
                "count of raw (1 XNO = 10**30 raw) and is never a float."
            ),
        },
        "interval_s": cfg["interval_s"],
        "timeout_ms": cfg["timeout_ms"],
        "poll_interval_ms": cfg["poll_ms"],
        "node": cfg["node"],
        "timing": {
            "starts": f"the moment the send RPC is {TIMING_START}",
            "stops": f"the moment the node {TIMING_STOP}",
            "not": [
                "not from block construction",
                "not from local signing",
                "not from the moment the send RPC returns",
            ],
            "resolution_ms": 1,
            "poll_note": (
                "Confirmation is detected by polling every "
                f"{cfg['poll_ms']}ms, so a measured elapsed_ms may overstate "
                f"the true one by up to {cfg['poll_ms']}ms and never "
                "understates it."
            ),
        },
        "percentiles": {
            "ranks": list(RANKS),
            "definition": (
                "Linear interpolation between the two closest ranks of the "
                "sorted confirmed samples: h = (n-1)*p/100, "
                "P = v[floor(h)] + (h-floor(h))*(v[ceil(h)]-v[floor(h)]), "
                "rounded to the nearest whole millisecond. This is the numpy "
                "and pandas default. Nearest-rank gives a different p99 from "
                "the same data; that is why this line exists."
            ),
            "computed_over": "confirmed samples only",
        },
        "confirmation_rate": {
            "definition": "confirmed / samples",
            "denominator": (
                "ALL samples in the window, including timeouts and errors. "
                "Nothing is excluded from the denominator."
            ),
            "format": (
                f"a decimal string with exactly {RATE_DP} decimal places, "
                "never a float"
            ),
        },
        "exclusions": {
            "from_the_denominator": "nothing",
            "from_the_percentiles": (
                "timeouts and errors, which have no elapsed_ms to include; "
                "they are counted separately and always shown beside the "
                "percentiles"
            ),
            "deleted": (
                "nothing inside the retention window -- a timeout is stored "
                "and served exactly like a confirmation"
            ),
        },
        "retention_days": round(cfg["retention_s"] / 86_400),
        "windows": sorted(samples.WINDOWS),
        "low_confidence_below_n": samples.LOW_CONFIDENCE_N,
        "reproduce_it_yourself": reproduce_command(cfg),
        "verify_our_numbers": (
            "Take any hundred block hashes from GET /v1/finality/samples and "
            "look them up on a node we do not run. Every confirmed sample "
            "carries the hash of the block it timed."
        ),
    }


def reproduce_command(cfg: dict) -> str:
    """One runnable command that takes one sample against the reader's node."""
    return (
        "# One sample against YOUR node. Set NODE to your own RPC endpoint.\n"
        "NODE=http://[::1]:7076\n"
        "HASH=$(curl -s -d '{\"action\":\"send\",\"wallet\":\"$WALLET\","
        "\"source\":\"$SRC\",\"destination\":\"$DST\",\"amount\":\"1\"}' "
        "\"$NODE\" | python3 -c 'import json,sys;print(json.load(sys.stdin)"
        "[\"block\"])')\n"
        "START=$(python3 -c 'import time;print(time.time())')\n"
        "until curl -s -d \"{\\\"action\\\":\\\"block_info\\\",\\\"json_block"
        "\\\":\\\"true\\\",\\\"hash\\\":\\\"$HASH\\\"}\" \"$NODE\" | "
        "grep -q '\"confirmed\": *\"true\"'; do sleep 0.05; done\n"
        "python3 -c \"import time;print(int((time.time()-$START)*1000),'ms')\"\n"
        "# The timer starts on the line before the send is dispatched and "
        "stops the first time the node reports the block confirmed, which is "
        "the same definition this service uses."
    )


def method_text(cfg: dict) -> str:
    data = method_json(cfg)
    lines = [
        "HOW THESE NUMBERS ARE MEASURED",
        "",
        data["what_is_measured"],
        "",
        "WHAT IS NOT MEASURED",
        data["what_is_not_measured"],
        "",
        "THE PAYMENT",
        f"  from            {data['payment']['from']}",
        f"  to              {data['payment']['to']}",
        f"  amount_raw      {data['payment']['amount_raw']}",
        f"  {data['payment']['note']}",
        "",
        "THE TIMING",
        f"  starts          {data['timing']['starts']}",
        f"  stops           {data['timing']['stops']}",
    ]
    lines += [f"  {n}" for n in data["timing"]["not"]]
    lines += [
        f"  {data['timing']['poll_note']}",
        "",
        "THE RUN",
        f"  interval        every {data['interval_s']}s",
        f"  timeout         {data['timeout_ms']}ms",
        f"  node            {data['node']}",
        f"  retention       {data['retention_days']} days",
        f"  windows         {', '.join(data['windows'])}",
        "",
        "PERCENTILES",
        f"  ranks           {', '.join('p%d' % r for r in data['percentiles']['ranks'])}",
        f"  computed over   {data['percentiles']['computed_over']}",
        f"  definition      {data['percentiles']['definition']}",
        "",
        "CONFIRMATION RATE",
        f"  definition      {data['confirmation_rate']['definition']}",
        f"  denominator     {data['confirmation_rate']['denominator']}",
        f"  format          {data['confirmation_rate']['format']}",
        "",
        "WHAT IS EXCLUDED",
        f"  from the denominator   {data['exclusions']['from_the_denominator']}",
        f"  from the percentiles   {data['exclusions']['from_the_percentiles']}",
        f"  deleted                {data['exclusions']['deleted']}",
        "",
        "CHECK OUR NUMBERS",
        f"  {data['verify_our_numbers']}",
        "",
        "REPRODUCE ONE SAMPLE AGAINST YOUR OWN NODE",
        data["reproduce_it_yourself"],
        "",
    ]
    return "\n".join(lines)
