#!/usr/bin/env python3
"""preflight.py - assert the four boundaries before they assert themselves.

Run it INSIDE your serving image. Check 2 imports the vllm your Triton backend
will import, and your laptop has a different one.

    python preflight.py --base-url http://router:8000/v1 --model my-model \\
                        --metrics http://metrics-proxy:9090/metrics \\
                        --vllm-version 0.11.0 --modules vllm.engine.metrics

Exit 0 = every check that RAN passed. Exit 1 = a failure, named.
Exit 2 = something was skipped, so the run proves less than it looks like it does.

    python preflight.py --self-test        # no GPU, no cluster, no openai package
    python preflight.py --only 3 --metrics file://$PWD/fixtures/metrics-healthy.txt

Boundaries 2 and 3 need no serving endpoint, and 3 works against a file:// URL,
so the committed fixtures make it runnable anywhere. Each check declares what it
needs and SKIPS with a reason when that is missing, instead of taking the other
three down with it. Before 2026-08-21 a missing `openai` package aborted the
whole run at import time, including the two boundaries that never touch it.
"""
import argparse, importlib, json, os, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

FAILS, SKIPS = [], []
ONLY = None


class Skip(Exception):
    """Raised by a check that cannot run. Never counted as a pass."""


def check(name):
    def wrap(fn):
        if ONLY and name.split()[0] not in ONLY:
            return
        try:
            fn()
            print(f"  PASS  {name}")
        except Skip as e:
            SKIPS.append(name)
            print(f"  SKIP  {name}: {e}")
        except Exception as e:
            FAILS.append(name)
            print(f"  FAIL  {name}: {e}")
    return wrap


def need_openai(base_url, api_key):
    """Import lazily and only for the checks that talk to an endpoint."""
    try:
        from openai import OpenAI
    except ImportError:
        raise Skip("the openai package is not installed in this image")
    if not base_url:
        raise Skip("no --base-url given")
    return OpenAI(base_url=base_url, api_key=api_key)


def scrape(url, metric):
    """One metric out of a Prometheus text exposition. None = not exported at all."""
    text = urllib.request.urlopen(url, timeout=10).read().decode()
    m = re.search(rf"^{re.escape(metric)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)$", text, re.M)
    return float(m.group(1)) if m else None


def run(a):
    JSON_PROBE = "Name the capital of France."     # no reason to answer in JSON on its own

    # BOUNDARY 1. The field your schema accepts and the code behind it may drop.
    # JSON coming back for a prompt that never asked for it proves response_format survived.
    @check("1 response_format survives the router")
    def _():
        client = need_openai(a.base_url, a.api_key)
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
        try:
            import vllm
        except ImportError:
            raise Skip("vllm is not importable here; run this inside your serving image")
        if not a.vllm_version:
            raise Skip("no --vllm-version given, so there is nothing to compare against")
        print(f"        vllm {vllm.__version__} from {vllm.__file__}")
        if vllm.__version__ != a.vllm_version:
            raise RuntimeError(f"pinned {a.vllm_version}, importing {vllm.__version__}")
        for mod in a.modules.split(","):
            importlib.import_module(mod.strip())

    # BOUNDARY 3. Nine metrics cross the bridge. These decide whether you get warned at all.
    @check("3 cache metrics reach your scrape target")
    def _():
        if not a.metrics:
            raise Skip("no --metrics given")
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
        client = need_openai(a.base_url, a.api_key)
        if not a.metrics:
            raise Skip("no --metrics given; boundary 4 is unprovable without the counter")
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


SELF_TEST = [
    # (fixture, expect) - expect is what boundary 3 must return on that scrape.
    ("fixtures/metrics-healthy.txt",       "PASS",
     "the golden scrape: every metric boundary 3 needs is present"),
    ("fixtures/metrics-pre-092.txt",       "FAIL",
     "pre-0.9.2 vLLM: the new cache-usage name does not exist yet"),
    ("fixtures/metrics-proxy-dropped.txt", "FAIL",
     "the proxy forwards the gauges and drops the prefix-cache counters"),
]


def self_test():
    """Boundary 3 against committed fixtures. No GPU, no cluster, no openai package.
    Two of the three MUST fail, or the check is not checking anything."""
    global FAILS, SKIPS, ONLY
    here = os.path.dirname(os.path.abspath(__file__))
    ok = True
    for rel, want, why in SELF_TEST:
        path = os.path.join(here, rel)
        if not os.path.exists(path):
            print(f"  FAIL  fixture missing: {rel}")
            ok = False
            continue
        FAILS, SKIPS, ONLY = [], [], {"3"}
        print(f"\n--- {rel}  (expect {want})")
        print(f"    {why}")
        run(argparse.Namespace(
            base_url=None, model=None, metrics="file://" + path, vllm_version=None,
            modules="vllm.engine.metrics", api_key="none", pressure=2, max_tokens=16))
        got = "FAIL" if FAILS else ("SKIP" if SKIPS else "PASS")
        good = got == want
        ok &= good
        print(f"    --> {'ok' if good else 'WRONG'}: got {got}, wanted {want}")

    # The guard has to guard: a scrape that finds nothing must never read as PASS.
    FAILS, SKIPS, ONLY = [], [], {"3"}
    print("\n--- an unreachable metrics endpoint  (expect FAIL, never PASS)")
    run(argparse.Namespace(
        base_url=None, model=None, metrics="file:///nonexistent-metrics-endpoint",
        vllm_version=None, modules="", api_key="none", pressure=2, max_tokens=16))
    good = bool(FAILS)
    ok &= good
    print(f"    --> {'ok' if good else 'WRONG'}: got {'FAIL' if FAILS else 'PASS/SKIP'}")

    print("\nself-test PASS" if ok else "\nself-test FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base-url")
    p.add_argument("--model")
    p.add_argument("--metrics")
    p.add_argument("--vllm-version")
    p.add_argument("--modules", default="vllm.engine.metrics")
    p.add_argument("--api-key", default="none")
    p.add_argument("--pressure", type=int, default=32, help="concurrent hogs for boundary 4")
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--only", help="comma-separated boundary numbers, e.g. --only 2,3")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args()
    if a.self_test:
        sys.exit(self_test())
    ONLY = {n.strip() for n in a.only.split(",")} if a.only else None
    run(a)
    if FAILS:
        print(f"\n  {len(FAILS)} unguarded: {', '.join(FAILS)}")
    elif SKIPS:
        print(f"\n  nothing failed, but {len(SKIPS)} check(s) never ran: {', '.join(SKIPS)}")
        print("  This run does not mean the boundaries are guarded. It means they are untested.")
    else:
        print("\n  four boundaries guarded")
    # 2, not 0: a skipped check is not a pass, and a CI job that treats it as one
    # is how a preflight ends up green against a stack it never reached.
    sys.exit(1 if FAILS else (2 if SKIPS else 0))
