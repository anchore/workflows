"""Glob pattern synthesis for instance selection."""

from __future__ import annotations

from . import inference, matching


def synthesize_globs(
    selected: list[dict],
    universe: list[dict],
    budget: float | None = None,
    nvme: bool | None = None,
) -> list[str]:
    """Extract globs from selected instances, refining if needed for constraints.

    1. Start with 2-char prefix globs (e.g., "c8*")
    2. If budget is set and a prefix glob includes selected instances over budget,
       try variant globs (e.g., "c8g*", "c8i*")
    3. If nvme=True and a glob would match non-NVMe instances in the full universe,
       refine to "d" variants to ensure glob only selects NVMe instances
    4. If constraints still can't be met, use exact instance names

    Note: Budget is checked against selected instances (since cpu/ram constraints
    handle further filtering), but nvme is checked against the full universe
    (to ensure the glob pattern itself only matches NVMe instances).
    """
    # group selected instances by prefix and variant
    by_prefix: dict[str, list[dict]] = {}
    for inst in selected:
        family = inference.get_family_prefix(inst["api_name"])
        prefix = family[:2]
        by_prefix.setdefault(prefix, []).append(inst)

    def max_price_for_glob_in_selected(glob: str) -> float:
        """Get max price of selected instances matching a glob pattern."""
        matches = [i for i in selected if matching.matches_family_pattern(i["api_name"], glob)]
        prices = [i["price"] for i in matches if i["price"] is not None]
        return max(prices) if prices else 0.0

    def glob_matches_non_nvme_in_universe(glob: str) -> bool:
        """Check if glob would match any non-NVMe instances in full universe.

        This ensures the glob pattern itself guarantees NVMe storage,
        regardless of other filters that might be applied.
        """
        matches = [i for i in universe if matching.matches_family_pattern(i["api_name"], glob)]
        return any(not i.get("nvme", False) for i in matches)

    def glob_is_valid(glob: str) -> bool:
        """Check if glob satisfies all constraints (budget, nvme)."""
        # budget: check against selected instances (other constraints handle filtering)
        if budget is not None and max_price_for_glob_in_selected(glob) > budget:
            return False
        # nvme: check against full universe (glob must guarantee NVMe)
        if nvme is True and glob_matches_non_nvme_in_universe(glob):
            return False
        return True

    result: list[str] = []

    for prefix, prefix_selected in sorted(by_prefix.items()):
        prefix_glob = f"{prefix}*"

        # check if prefix glob satisfies all constraints
        if glob_is_valid(prefix_glob):
            result.append(prefix_glob)
            continue

        # prefix doesn't satisfy constraints - try variant globs
        variants: dict[str, list[dict]] = {}
        for inst in prefix_selected:
            variant = inference.get_family_prefix(inst["api_name"])
            variants.setdefault(variant, []).append(inst)

        for variant, variant_selected in sorted(variants.items()):
            variant_glob = f"{variant}*"

            # check if variant glob satisfies constraints
            if glob_is_valid(variant_glob):
                result.append(variant_glob)
            else:
                # variant doesn't satisfy constraints - use exact instance names
                for inst in variant_selected:
                    result.append(inst["api_name"])

    # remove globs that are subsumed by other globs
    # e.g., if we have m5d* and m5dn*, remove m5dn* since m5d* already matches it
    unique = sorted(set(result))
    final = []
    for glob in unique:
        # check if this glob is subsumed by any other glob
        subsumed = False
        for other in unique:
            if other != glob and _glob_subsumes(other, glob):
                subsumed = True
                break
        if not subsumed:
            final.append(glob)

    return final


def _glob_subsumes(broader: str, narrower: str) -> bool:
    """Check if broader glob pattern subsumes the narrower one.

    e.g., 'm5d*' subsumes 'm5dn*' because m5d* matches m5dn.xlarge
    """
    # only wildcard patterns can subsume
    if not broader.endswith("*"):
        return False

    # exact names can't be subsumed by patterns (they're precise)
    if "." in narrower and not narrower.endswith("*"):
        return False

    # strip wildcards for comparison
    broad_prefix = broader.rstrip("*")
    narrow_prefix = narrower.rstrip("*")

    # broader subsumes narrower if narrow starts with broad
    # e.g., 'm5d' subsumes 'm5dn' -> 'm5d*' subsumes 'm5dn*'
    return narrow_prefix.startswith(broad_prefix) and narrow_prefix != broad_prefix
