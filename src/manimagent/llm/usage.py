"""Normalize documented supplier usage without treating missing cache data as a miss."""

from __future__ import annotations

from manimagent.schemas import Usage


def token_count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(raw) -> dict:
    usage = raw if isinstance(raw, dict) else {}
    prompt = token_count(usage.get("prompt_tokens"))
    output = token_count(usage.get("completion_tokens"))
    candidates = []
    invalid = False

    # MiMo and compatible Chat Completions responses use this documented nested field.
    if "prompt_tokens_details" in usage:
        details = usage["prompt_tokens_details"]
        if not isinstance(details, dict):
            invalid = True
        elif "cached_tokens" in details:
            cached = token_count(details["cached_tokens"])
            if cached is None:
                invalid = True
            else:
                candidates.append(cached)

    # DeepSeek reports hit and miss as a pair. Reject partial or inconsistent pairs.
    if "prompt_cache_hit_tokens" in usage or "prompt_cache_miss_tokens" in usage:
        hit = token_count(usage.get("prompt_cache_hit_tokens"))
        miss = token_count(usage.get("prompt_cache_miss_tokens"))
        if hit is None or miss is None or prompt is None or hit + miss != prompt:
            invalid = True
        else:
            candidates.append(hit)

    cached = (
        candidates[0]
        if candidates
        and not invalid
        and prompt is not None
        and all(value == candidates[0] and value <= prompt for value in candidates)
        else None
    )
    return {
        "input_tokens": prompt,
        "output_tokens": output,
        "cached_input_tokens": cached,
        "cache_reported": cached is not None,
        "cache_accounted_input_tokens": prompt if cached is not None else None,
    }


def accumulate_usage(total: Usage, normalized: dict) -> None:
    for name in ("input_tokens", "output_tokens"):
        count = normalized[name]
        if count is not None:
            setattr(total, name, getattr(total, name) + count)
            total.tokens_reported = True
    if normalized["cache_reported"]:
        total.cached_input_tokens = (total.cached_input_tokens or 0) + normalized[
            "cached_input_tokens"
        ]
        total.cache_reported_calls += 1
        total.cache_accounted_input_tokens += normalized["cache_accounted_input_tokens"]
