# The four fixes, in the order they usually bite

## Boundary 2 first, because it stops the container from starting

`compose.yml` pins `vllm==0.11.1`, which deleted `vllm.engine.metrics`. Triton 25.09
imports that module at backend load, so nothing serves.

Fix: pin `vllm==0.11.0`, the last release that still carries it. Then assert the import in
CI so the next pair of releases fails your build instead of your cluster.

## Boundary 1, the field your schema accepts and your code drops

`router/main.py` declares `response_format` in its request model and never passes it to the
engine. A caller gets a 200 with prose in the body and no error anywhere.

Fix: forward the field into vLLM's guided decoding settings. One mapping, and a test that
sends a prompt with no reason to answer in JSON and asserts JSON comes back.

## Boundary 3, the metrics that never cross the bridge

`prometheus.yml` scrapes Triton, which forwards 9 of vLLM's 40-plus metrics and drops the
two that warn you: how full the KV cache runs and how often a prefix is reused.

Fix: scrape the merge proxy instead, then alert on `vllm:kv_cache_usage_perc` above 0.90
sustained. Chart `vllm:prefix_cache_hits_total` against `vllm:prefix_cache_queries_total`.

## Boundary 4, the state that assumes the token list only grows

`tracker/format_state.py` appends every token it sees. Under memory pressure vLLM evicts a
half-finished request and reschedules it with a shorter list, and the tracker never notices.

Fix: compare lengths between decode steps. When the list shrinks, throw the state away and
rebuild it from the prompt.
