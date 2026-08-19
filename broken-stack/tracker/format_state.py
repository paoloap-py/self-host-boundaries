"""BOUNDARY 4. A per-request format tracker that, when BREAK_TRACKER=1, assumes the
token list only grows.

Under memory pressure vLLM evicts a partially completed request, drops its KV cache,
and reschedules it later, folding the tokens it already generated back into the prompt.
The list your code sees comes back SHORTER. The broken path appends regardless, so after
a preemption it holds a state machine that no longer matches the model's actual output.

No error mentions it, because only your code holds the assumption.

Set BREAK_TRACKER=0 to take the fixed path: notice the shrink, throw the state machine
away, and rebuild from the prompt.

    python -m tracker.format_state      # replays a preemption against both paths
"""
import os

BROKEN = os.environ.get("BREAK_TRACKER", "1") == "1"


class FormatTracker:
    def __init__(self, prompt: list[int]):
        self.prompt = prompt
        self.seen: list[int] = []
        self.depth = 0

    def observe(self, tokens: list[int]) -> None:
        # THE FIX for boundary 4. A shrink is the only notice the engine gives you.
        if not BROKEN and len(tokens) < len(self.seen):
            self.seen, self.depth = [], 0

        new = tokens[len(self.seen):]
        for t in new:
            self.depth += 1 if t == 123 else (-1 if t == 125 else 0)
        self.seen = list(tokens)

    def snapshot(self) -> tuple:
        return (len(self.seen), self.depth)


if __name__ == "__main__":
    # One preemption, replayed. The engine opens two objects, gets evicted at step 4,
    # and reschedules with the generated tokens folded back into the prompt, so the
    # list it hands you is shorter than the one you saw a step ago.
    steps = [[123], [123, 97], [123, 97, 123], [123, 97, 123, 98], [123, 97]]
    t = FormatTracker(prompt=[1, 2, 3])
    for s in steps:
        t.observe(s)
    tokens, depth = t.snapshot()
    print(f"BREAK_TRACKER={os.environ.get('BREAK_TRACKER', '1')}  "
          f"tokens={tokens} depth={depth}  (truth: tokens=2 depth=1)")
    print("The broken path double-counts the brace it already saw, so every check that "
          "reads depth is now wrong for the rest of the request.")
