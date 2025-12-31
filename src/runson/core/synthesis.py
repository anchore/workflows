"""Glob pattern synthesis for instance selection."""

from __future__ import annotations

from . import inference, matching


def synthesize_globs(
    selected: list[dict],
    universe: list[dict],
    budget: float | None = None,
) -> list[str]:
    """Extract globs from selected instances, refining if needed to stay under budget.

    1. Start with 2-char prefix globs (e.g., "c8*")
    2. If budget is set and a prefix glob includes universe instances over budget,
       try variant globs (e.g., "c8g*", "c8i*")
    3. If variants still exceed budget, use exact instance names
    """
    # group selected instances by prefix and variant
    by_prefix: dict[str, list[dict]] = {}
    for inst in selected:
        family = inference.get_family_prefix(inst["api_name"])
        prefix = family[:2]
        by_prefix.setdefault(prefix, []).append(inst)

    # index universe by prefix and variant for price lookups
    universe_by_prefix: dict[str, list[dict]] = {}
    universe_by_variant: dict[str, list[dict]] = {}
    for inst in universe:
        family = inference.get_family_prefix(inst["api_name"])
        prefix = family[:2]
        universe_by_prefix.setdefault(prefix, []).append(inst)
        universe_by_variant.setdefault(family, []).append(inst)

    def max_price_for_glob(glob: str) -> float:
        """Get max price of all universe instances matching a glob pattern."""
        matches = [i for i in universe if matching.matches_family_pattern(i["api_name"], glob)]
        prices = [i["price"] for i in matches if i["price"] is not None]
        return max(prices) if prices else 0.0

    result: list[str] = []

    for prefix, prefix_selected in sorted(by_prefix.items()):
        prefix_glob = f"{prefix}*"

        # check if prefix glob is under budget (or no budget set)
        if budget is None or max_price_for_glob(prefix_glob) <= budget:
            result.append(prefix_glob)
            continue

        # prefix exceeds budget - try variant globs
        variants: dict[str, list[dict]] = {}
        for inst in prefix_selected:
            variant = inference.get_family_prefix(inst["api_name"])
            variants.setdefault(variant, []).append(inst)

        for variant, variant_selected in sorted(variants.items()):
            variant_glob = f"{variant}*"

            # check if variant glob is under budget
            if max_price_for_glob(variant_glob) <= budget:
                result.append(variant_glob)
            else:
                # variant still exceeds budget - use exact instance names
                for inst in variant_selected:
                    result.append(inst["api_name"])

    return sorted(set(result))
