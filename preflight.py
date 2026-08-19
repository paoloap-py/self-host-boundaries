#!/usr/bin/env python3
"""preflight.py - assert the four boundaries before they assert themselves.

Run it INSIDE your serving image. Check 2 imports the vllm your Triton backend
will import, and your laptop has a different one.

    python preflight.py --base-url http://router:8000/v1 --model my-model \\
                        --metrics http://metrics-proxy:9090/metrics \\
                        --vllm-version 0.11.0 --modules vllm.engine.metrics

Exit 0 = all four guarded. Exit 1 = the first failure, named.
"""
import argparse, importlib, json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

FAILS = []


def check(name):
    def wrap(fn):
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:
            FAILS.append(name)
            print(f"  FAIL  {name}: {e}")
    return wrap


def scrape(url, metric):
    """One metric out of a Prometheus text exposition. None = not exported at all."""
    text = urllib.request.urlopen(url, timeout=10).read().decode()
    m = re.search(rf"^{re.escape(metric)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)$", text, re.M)
    return float(m.group(1)) if m else None


def run(a):
    from openai import OpenAI
    client = OpenAI(base_url=a.base_url, api_key=a.api_key)
    JSON_PROBE = "Name the capital of France."     # no reason to answer in JSON on its own

    # BOUNDARY 1. The field your schema accepts and the code behind it may drop.
    # JSON coming back for a prompt that never asked for it proves response_format survived.
    @check("1 response_format survives the router")
    def _():
        for _ in range(2):        # twice, so boundary 3 has a repeated prefix to hit on
            r = client.chat.completions.create(
                model=a.model, messages=[{"role": "user", "content": JSON_PROBE}],
                response_format={"type": "json_object"}, max_tokens=64,
            )
        json.loads(r.choices[0].message.content)   # raises on prose, which is the point

    # BOUNDARY 2. Two release calendars, one import, no warning. List your real imports:
    #   grep -rhoE '^[[:space:]]*(from|import) vllm[.a-z_]*' src/ | awk '{print $2}' | sort -u
    @check("2 the backend's vllm imports resolve")
    def _():
        import vllm
        print(f"        vllm {vllm.__version__} from {vllm.__file__}")
        if vllm.__version__ != a.vllm_version:
            raise RuntimeError(f"pinned {a.vllm_version}, importing {vllm.__version__}")
        for mod in a.modules.split(","):
            importlib.import_module(mod.strip())

    # BOUNDARY 3. Nine metrics cross the bridge. These decide whether you get warned at all.
    @check("3 cache metrics reach your scrape target")
    def _():
        usage = scrape(a.metrics, "vllm:kv_cache_usage_perc")
        if usage is None:
            legacy = scrape(a.metrics, "vllm:gpu_cache_usage_perc")
            raise RuntimeError("kv_cache_usage_perc missing" + (
                " (gpu_cache_usage_perc is there: you are pre-0.9.2, alert on that name)"
                if legacy is not None else " and so is the pre-0.9.2 name"))
        for m in ("vllm:prefix_cache_queries_total", "vllm:prefix_cache_hits_total"):
            if scrape(a.metrics, m) is None:
                raise RuntimeError(f"{m} never reaches your dashboard")

    # BOUNDARY 4. The only one you cannot find by reading code. Fill the KV cache on
    # purpose, then read the engine's own preemption counter to PROVE the path ran, and
    # check whether the rewind leaked into what your endpoint streamed. A pass here with
    # a flat counter proves nothing, so this fails loudly instead of quietly passing.
    @check("4 a forced preemption does not leak through your endpoint")
    def _():
        before = scrape(a.metrics, "vllm:num_preemptions_total")
        if before is None:
            raise RuntimeError("num_preemptions_total missing: fix boundary 3 first")

        def hog(_):
            return client.chat.completions.create(
                model=a.model, max_tokens=a.max_tokens,
                messages=[{"role": "user", "content": "Write an essay about caching. " * 64}],
            )

        with ThreadPoolExecutor(max_workers=a.pressure) as pool:
            watched = pool.submit(lambda: "".join(
                c.choices[0].delta.content or "" for c in client.chat.completions.create(
                    model=a.model, max_tokens=a.max_tokens, stream=True,
                    messages=[{"role": "user", "content": "Count slowly from 1 to 200."}])))
            list(pool.map(hog, range(a.pressure - 1)))
            text = watched.result()

        after = scrape(a.metrics, "vllm:num_preemptions_total")
        if after <= before:
            raise RuntimeError(f"no preemption fired ({before:.0f} -> {after:.0f}); this "
                               f"check proved nothing. Raise --pressure or --max-tokens "
                               f"until the counter moves, then trust the result.")
        # a leak shows up as the stream re-emitting a run it already sent
        for n in range(0, max(0, len(text) - 48), 16):
            if text.count(text[n:n + 48]) > 1:
                raise RuntimeError(f"stream repeated a 48-char run after {after - before:.0f} "
                                   f"preemption(s): your endpoint is leaking the rewind")
        print(f"        {after - before:.0f} preemption(s) forced, stream stayed clean")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--metrics", required=True)
    p.add_argument("--vllm-version", required=True)
    p.add_argument("--modules", default="vllm.engine.metrics")
    p.add_argument("--api-key", default="none")
    p.add_argument("--pressure", type=int, default=32, help="concurrent hogs for boundary 4")
    p.add_argument("--max-tokens", type=int, default=1024)
    run(p.parse_args())
    print(f"\n  {len(FAILS)} unguarded: {', '.join(FAILS)}" if FAILS
          else "\n  four boundaries guarded")
    sys.exit(1 if FAILS else 0)
