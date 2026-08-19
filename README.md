# The Four Boundaries, Broken On Purpose

> **Status: written from the vendors' own documentation and not yet run end to end.**
> The versions, module names and metric names are verified against current docs. The
> compose file needs a GPU to run for real. Open an issue if a pin has drifted.

Self-hosting an LLM replaces one vendor contract with four handoffs you now own. Each one
fails silently. This repo breaks all four on purpose so you can watch `preflight.py` catch
them, then point the same script at your own stack knowing it works.

Companion to [What Breaks When You Self-Host an LLM](https://theaiengineer.substack.com/)
in The AI Engineer.

## The four boundaries, and how each is broken here

| # | boundary | what is wrong in `broken-stack/` |
|---|---|---|
| 1 | your caller to the router | the router accepts `response_format` and never forwards it |
| 2 | the manager to the engine | Triton 25.09 pinned against a vLLM that deleted `vllm.engine.metrics` |
| 3 | the engine to your dashboard | the metrics bridge is up, the two alerting metrics never cross it |
| 4 | the engine to your own code | the format tracker assumes the token list only grows |

## Watch all four fail

```bash
docker compose -f broken-stack/compose.yml up -d
python preflight.py --base-url http://localhost:8000/v1 --model research \
  --metrics http://localhost:9090/metrics --vllm-version 0.11.0
```

Expected on a first run: four FAILs, one per boundary, each naming what it found.

## Then fix them one at a time

Each fix is a single line, and `broken-stack/FIXES.md` has them in order. Apply one, re-run
the script, watch that check go green. The point is not the fixes. The point is that you
now have a script that fails loudly on a stack you know is broken, which is the only way to
trust it against a stack you hope is fine.

## Point it at production

```bash
python preflight.py --base-url https://your-router/v1 --model your-model \
  --metrics https://your-metrics-proxy/metrics --vllm-version 0.11.0 \
  --modules vllm.engine.metrics
```

Run it in CI. It exits non-zero on the first unguarded boundary.
