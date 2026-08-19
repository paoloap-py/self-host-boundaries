"""BOUNDARY 4. A per-request format tracker that assumes the token list only grows.

Under memory pressure vLLM evicts a partially completed request, drops its KV cache,
and reschedules it later, folding the tokens it already generated back into the prompt.
The list your code sees comes back SHORTER. This tracker appends regardless, so after a
preemption it holds a state machine that no longer matches the model's actual output.

No error mentions it, because only your code holds the assumption.
"""


class FormatTracker:
    def __init__(self, prompt: list[int]):
        self.prompt = prompt
        self.seen: list[int] = []
        self.depth = 0

    def observe(self, tokens: list[int]) -> None:
        # BROKEN: appends whatever is new, and never checks whether the list shrank.
        #
        # Fix:
        #   if len(tokens) < len(self.seen):
        #       self.__init__(self.prompt)      # rebuild, do not resume
        new = tokens[len(self.seen):]
        for t in new:
            self.depth += 1 if t == 123 else (-1 if t == 125 else 0)
        self.seen = list(tokens)

    def snapshot(self) -> tuple:
        return (len(self.seen), self.depth)
