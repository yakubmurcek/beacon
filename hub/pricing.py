"""API list prices, USD per million tokens.

Sources: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-11-22: Fable 5.2, Sonnet 5.5, Haiku 5.5),
https://platform.minimax.io/docs/guides/pricing-paygo.md (checked 2026-09-26).
Only models with a verified public price from their maker are listed. Anything else counts as
unpriced unless the agent itself logged a cost (Grok, OpenCode do).

Columns: input, 5m cache write, 1h cache write, cache read, output.
"""
import functools
import re

CLAUDE = [
    # (pattern, input, write_5m, write_1h, read, output)
    (r'fable-5-2|fable-5\.2|mythos-5-2', 8.0, 10.00, 16.0, 0.20, 40.0),
    (r'fable-5-1|fable-5\.1|mythos-5-1', 10.0, 12.50, 20.0, 0.25, 50.0),
    (r'fable-5|mythos-5', 10.0, 12.50, 20.0, 1.00, 50.0),
    (r'opus-5-5|opus-5\.5', 4.0, 5.00, 8.0, 0.20, 20.0),
    (r'opus-5|opus-4-[5-8]|opus-4\.[5-8]', 5.0, 6.25, 10.0, 0.50, 25.0),
    (r'opus-4', 15.0, 18.75, 30.0, 1.50, 75.0),
    (r'sonnet-5-5|sonnet-5\.5', 2.0, 2.50, 4.0, 0.10, 10.0),
    (r'sonnet-5', 2.0, 2.50, 4.0, 0.20, 10.0),
    (r'sonnet-4', 3.0, 3.75, 6.0, 0.30, 15.0),
    (r'haiku-5-5|haiku-5\.5', 0.10, 0.125, 0.20, 0.01, 0.50),  # prompts up to 100k tokens: LONG has the rest
    (r'haiku-4-5|haiku-4\.5|4\.5-haiku', 1.0, 1.25, 2.0, 0.10, 5.0),
    (r'haiku-3-5|3\.5-haiku', 0.8, 1.00, 1.6, 0.08, 4.0),
]

# Other makers' own list prices. MiniMax-M3: standard tier up to 512k input (logged calls
# stay under 310k); it lists no cache-write price and its calls log no cache writes.
OTHER = [
    (r'minimax-m3(?![.\d])', 0.30, 0.30, 0.30, 0.06, 1.20),
]

# Priced by prompt length: a call whose prompt (input + cache reads + cache writes) is over the limit
# pays these for all its tokens.
LONG = [
    (r'haiku-5-5|haiku-5\.5', 100_000, (0.50, 0.625, 1.0, 0.05, 2.50)),
]

# Fast mode replaces the base input/output price; cache multipliers stack on top.
FAST = [
    (r'fable-5-2|fable-5\.2|mythos-5-2', 16.0, 80.0),
    (r'opus-5-5', 8.0, 40.0),
    (r'opus-5|opus-4-8', 10.0, 50.0),
]


@functools.lru_cache(maxsize=512)
def rates(model, speed='standard'):
    """Return (input, write_5m, write_1h, read, output) per token, or None if unpriced."""
    m = (model or '').lower()
    if 'claude' not in m and not re.search(r'opus|sonnet|haiku|fable|mythos', m):
        for pat, i, w5, w1, r, o in OTHER:
            if re.search(pat, m):
                return tuple(x / 1e6 for x in (i, w5, w1, r, o))
        return None
    for pat, i, w5, w1, r, o in CLAUDE:
        if re.search(pat, m):
            if speed == 'fast':
                for fpat, fi, fo in FAST:
                    if re.search(fpat, m):
                        w5, w1, r = w5 / i * fi, w1 / i * fi, r / i * fi
                        i, o = fi, fo
                        break
            return tuple(x / 1e6 for x in (i, w5, w1, r, o))
    return None


def cost(model, speed, input=0, cache_write_5m=0, cache_write_1h=0, cache_read=0, output=0):
    """API list cost in USD, or None if the model has no known price."""
    r = rates(model, speed)
    if r is None:
        return None
    r = long_rates(model, input + cache_write_5m + cache_write_1h + cache_read) or r
    return (input * r[0] + cache_write_5m * r[1] + cache_write_1h * r[2]
            + cache_read * r[3] + output * r[4])


def long_rates(model, prompt):
    m = (model or '').lower()
    for pat, limit, prices in LONG:
        if prompt > limit and re.search(pat, m):
            return tuple(x / 1e6 for x in prices)
    return None


def uncached_cost(model, speed, input=0, cache_write_5m=0, cache_write_1h=0, cache_read=0, output=0):
    """What the same calls would cost with every cached token billed as fresh input."""
    r = rates(model, speed)
    if r is None:
        return None
    r = long_rates(model, input + cache_write_5m + cache_write_1h + cache_read) or r
    return (input + cache_write_5m + cache_write_1h + cache_read) * r[0] + output * r[4]


@functools.lru_cache(maxsize=512)
def family(model):
    m = (model or '').lower()
    for key in ('fable', 'mythos', 'opus', 'sonnet', 'haiku'):
        if key in m:
            return key.capitalize()
    for key, name in (('grok', 'Grok'), ('gpt', 'GPT'), ('codex', 'GPT'), ('gemini', 'Gemini'),
                      ('minimax', 'MiniMax'), ('deepseek', 'DeepSeek'), ('glm', 'GLM'),
                      ('qwen', 'Qwen'), ('muse', 'Muse'), ('mimo', 'MiMo'), ('stealth', 'Stealth')):
        if key in m:
            return name
    return 'Other'
