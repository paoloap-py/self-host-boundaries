# The Four Boundaries, Broken On Purpose

> **Status: written from the vendors' own documentation and not yet run end to end.**
> The versions, module names and metric names are verified against current docs. The
> compose file needs a GPU to run for real. Open an issue if a pin has drifted.

Self-hosting an LLM replaces one vendor contract with four handoffs you now own. Each one
fails silently: no exception, no log line, and a status code that says everything worked.
This repo breaks all four on purpose so you can watch `preflight.py` catch them, then point
the same script at your own stack knowing it works.

A check that has never failed on a stack you know is broken tells you nothing about a stack
you hope is fine. That is what this is for.

Companion to [What Breaks When You Self-Host an LLM](https://theaiengineer.substack.com/)
in The AI Engineer.

## The four boundaries

Each boundary sits between two components with different owners, and neither owner is
watching it. That is what separates a boundary from an interface: an interface has one
owner, a boundary has two.

| # | boundary | how it fails | what is wrong here |
|---|---|---|---|
| 1 | your caller to the router | a 200 with prose in the body | `router/main.py` accepts `response_format` and never forwards it |
| 2 | the manager to the engine | the backend never loads | `compose.yml` pins a vLLM release that deleted `vllm.engine.metrics` |
| 3 | the engine to your dashboard | the graph looks healthy | `prometheus-1.yml` scrapes Triton alone, which drops the two metrics that warn you |
| 4 | the engine to your own code | your counters silently go wrong | `tracker/format_state.py` assumes the token list only grows |

**Boundary 1.** Your schema accepts `response_format` because it is a valid field. The code
behind it drops the field before the engine sees it, so nothing holds the model to JSON and
nothing explains why. A field your schema accepts is a promise your schema cannot keep, and
the code behind it is the only thing that decides.

**Boundary 2.** Triton imports vLLM modules by name. A patch bump is the smallest release a
project can cut, and this one takes away a module your other project imports at startup.
Your version risk lives in the widest gap between two release calendars, never in the size
of the bump.

**Boundary 3.** vLLM exports more than forty metrics. Triton forwards nine of them, and
neither `vllm:kv_cache_usage_perc` nor `vllm:prefix_cache_hits_total` is among the nine. The
dashboard you built on what crossed the bridge cannot show you the two things that predict
the outage.

**Boundary 4.** When GPU memory runs out, vLLM evicts a half-finished request, drops its KV
cache, and reschedules it with the generated tokens folded back into the prompt. The token
list you are tracking arrives **shorter** than it was one step ago, and that shrink is the
only notice you get. Any state you keep outside the engine has to survive the engine
restarting a request without telling you.

## Break all four at once

```bash
docker compose -f broken-stack/compose.yml up -d
python preflight.py --base-url http://localhost:8000/v1 --model research \
  --metrics http://localhost:9090/metrics --vllm-version 0.11.0
```

Four FAILs, one per boundary, each naming what it found.

## Or break exactly one

Every boundary is behind its own flag, and all four default to broken. Copy `.env.example`
to `.env`, set one flag to `0`, and that boundary is healthy while the rest stay broken.

| flag | `=1` (default) | `=0` |
|---|---|---|
| `BREAK_ROUTER` | drops `response_format` | maps it onto `guided_json` |
| `BREAK_VERSION_PIN` | `vllm==0.11.1`, nothing serves | `vllm==0.11.0`, Triton loads |
| `BREAK_METRICS` | scrapes Triton alone | also scrapes the merge proxy |
| `BREAK_TRACKER` | appends blindly | rebuilds on a shrink |

This is the direction that tests your own monitoring. Break one boundary, leave the rest
healthy, and see whether anything you already run notices.

Boundary 4 needs no GPU and no containers:

```bash
BREAK_TRACKER=1 python -m tracker.format_state    # double-counts a brace after the eviction
BREAK_TRACKER=0 python -m tracker.format_state    # rebuilds, and the depth is right
```

## Then fix them one at a time

`broken-stack/FIXES.md` has the four repairs in the order they usually bite, starting with
the version pin because that one stops the container from starting. Apply one, re-run the
script, watch that check go green.

The point is not the fixes. The point is that you end up with a script that fails loudly on
a stack you know is broken.

## Point it at production

```bash
python preflight.py --base-url https://your-router/v1 --model your-model \
  --metrics https://your-metrics-proxy/metrics --vllm-version 0.11.0 \
  --modules vllm.engine.metrics
```

Run it in CI. It exits non-zero on the first unguarded boundary, and the next pair of
releases breaks your build instead of your cluster.
