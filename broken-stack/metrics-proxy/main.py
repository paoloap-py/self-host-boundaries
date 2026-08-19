"""The merge proxy boundary 3 needs, and the thirty lines you own from day one the
moment you run vLLM under Triton.

Triton forwards 9 of vLLM's 40-plus metrics onto its own /metrics surface, and neither
of the two that warn you is among them: KV cache utilisation and prefix cache hit rate.
vLLM writes all of them into PROMETHEUS_MULTIPROC_DIR instead. This reads that directory
with prometheus_client's MultiProcessCollector and serves the full set on :9091, so one
scrape config reaches both.
"""
import os
from prometheus_client import CollectorRegistry, multiprocess, make_wsgi_app
from wsgiref.simple_server import make_server

os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp/vllm-metrics")
os.makedirs(os.environ["PROMETHEUS_MULTIPROC_DIR"], exist_ok=True)

registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)

if __name__ == "__main__":
    make_server("", 9091, make_wsgi_app(registry)).serve_forever()
