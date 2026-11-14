from curator_curation.llm import ratelimit


def test_bucket_waits_for_tokens_instead_of_overspending():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    bucket = ratelimit.TokenBucket(600, clock=lambda: now[0], sleep=sleep)
    bucket.acquire(600)
    bucket.acquire(100)
    assert round(sum(slept), 6) == 10  # 600 tokens/minute refills 100 tokens in 10 s
    bucket.acquire(10_000)  # larger than capacity: waits for a full bucket, never forever
    assert round(now[0], 6) == 70


def test_estimate_counts_text_and_images():
    blocks = [{"type": "text", "text": "x" * 400}, {"type": "image", "data": "y" * 10_000}]
    text = len('[{"type": "text", "text": "' + "x" * 400 + '"}]') + len("{}")
    assert ratelimit.estimate(blocks, {}) == int(text / ratelimit.CHARS_PER_TOKEN) + ratelimit.IMAGE_TOKENS
    assert ratelimit.bucket("m", 0) is None
    assert ratelimit.bucket("m", 100) is ratelimit.bucket("m", 100)
