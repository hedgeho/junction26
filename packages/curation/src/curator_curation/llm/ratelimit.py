"""Process-wide input-token budget per model, so concurrent readers queue instead of exhausting provider quota."""

import json
import threading
import time

IMAGE_TOKENS = 260
CHARS_PER_TOKEN = 3.3


class TokenBucket:
    def __init__(self, tokens_per_minute: int, clock=time.monotonic, sleep=time.sleep):
        self.capacity = float(tokens_per_minute)
        self.available = float(tokens_per_minute)
        self.rate = tokens_per_minute / 60
        self.clock, self.sleep = clock, sleep
        self.updated = clock()
        self.lock = threading.Lock()

    def acquire(self, tokens: float) -> None:
        tokens = min(tokens, self.capacity)
        while True:
            with self.lock:
                now = self.clock()
                self.available = min(self.capacity, self.available + (now - self.updated) * self.rate)
                self.updated = now
                if self.available >= tokens:
                    self.available -= tokens
                    return
                wait = (tokens - self.available) / self.rate
            self.sleep(wait)


_buckets: dict[str, TokenBucket] = {}
_lock = threading.Lock()


def bucket(model: str, tokens_per_minute: int) -> TokenBucket | None:
    if tokens_per_minute <= 0:
        return None
    with _lock:
        if model not in _buckets:
            _buckets[model] = TokenBucket(tokens_per_minute)
        return _buckets[model]


def estimate(input_value, questions: dict) -> int:
    """Rough input tokens, erring high: ~3.3 characters per token for text, a flat amount per image."""
    blocks = input_value if isinstance(input_value, list) else [input_value]
    images = [b for b in blocks if isinstance(b, dict) and b.get("type") == "image"]
    text = [b for b in blocks if b not in images]
    chars = len(json.dumps(text, ensure_ascii=False)) + len(json.dumps(questions, ensure_ascii=False))
    return int(chars / CHARS_PER_TOKEN) + len(images) * IMAGE_TOKENS
