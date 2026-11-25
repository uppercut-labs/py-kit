"""Numbers worth reporting about a run: gate pass rate, throughput that counts only accepted output, diversity."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, Mapping, Sequence


def output_diversity(outputs: Iterable[str]) -> Dict[str, Any]:
    """How many accepted outputs are distinct. Whitespace around an output is ignored.

    Returns `accepted`, `unique_outputs`, `duplicate_count` and `diversity_ratio` (1.0 for no outputs).
    A gate can pass a model that returns the same text for every piece; this shows it.
    """
    digests = [hashlib.sha256(text.strip().encode("utf-8")).hexdigest() for text in outputs]
    unique = len(set(digests))
    return {
        "accepted": len(digests),
        "unique_outputs": unique,
        "duplicate_count": len(digests) - unique,
        "diversity_ratio": round(unique / len(digests), 4) if digests else 1.0,
    }


def summarize_samples(samples: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Quality-adjusted throughput for one model on one task.

    Each sample is a mapping with `passed` (the gate verdict) and `wall_clock_s`, and optionally `ttft_s`
    (seconds to the first token, or None) and `eval_tokens_per_s` (0 when unknown). Returns:

    - `pass_rate`: passed over total, two places.
    - `accepted_outputs_per_s`: passed samples over the total wall clock, three places.
    - `mean_wall_clock_s` and `mean_ttft_s`: three places; the mean skips samples without a first-token time.
    - `mean_tokens_per_s`: the mean over samples with a positive speed, two places.
    - `quality_adjusted_tokens_per_s`: `mean_tokens_per_s * pass_rate`, so a fast model whose output keeps
      failing the gate scores low.
    """
    total = len(samples)
    passed = sum(1 for s in samples if s.get("passed"))
    pass_rate = round(passed / total, 2) if total else 0.0
    clocks = [float(s.get("wall_clock_s") or 0.0) for s in samples]
    total_clock = sum(clocks)
    ttfts = [s["ttft_s"] for s in samples if s.get("ttft_s") is not None]
    speeds = [s["eval_tokens_per_s"] for s in samples if (s.get("eval_tokens_per_s") or 0) > 0]
    mean_speed = round(sum(speeds) / len(speeds), 2) if speeds else 0.0
    return {
        "total_samples": total,
        "passed_samples": passed,
        "pass_rate": pass_rate,
        "accepted_outputs_per_s": round(passed / total_clock, 3) if total_clock > 0 else 0.0,
        "mean_ttft_s": round(sum(ttfts) / len(ttfts), 3) if ttfts else 0.0,
        "mean_wall_clock_s": round(total_clock / len(clocks), 3) if clocks else 0.0,
        "mean_tokens_per_s": mean_speed,
        "quality_adjusted_tokens_per_s": round(mean_speed * pass_rate, 2),
    }
