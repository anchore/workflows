"""Instance and runner matching logic."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runson.cli.config import Requirement


def matches_family_pattern(api_name: str, pattern: str) -> bool:
    """Check if instance API name matches a single family pattern.

    Patterns:
    - 'r7' or 'r7*' -> matches r7, r7i, r7a, r7g, etc.
    - 'r7a' or 'r7a*' -> matches only r7a variants
    - 'r7a.large' -> exact match
    """
    if not api_name:
        return False

    # strip trailing wildcard for processing
    clean_pattern = pattern.rstrip("*")

    # exact match (includes size, e.g., 'r7a.large')
    if "." in clean_pattern:
        return api_name.lower() == clean_pattern.lower()

    # family prefix match
    # pattern 'r7' should match r7, r7i, r7a, r7g, etc.
    regex = rf"^{re.escape(clean_pattern)}[a-z0-9]*\."
    return bool(re.match(regex, api_name, re.IGNORECASE))


def matches_any_pattern(api_name: str, patterns: list[str]) -> bool:
    """Check if instance matches any of the given patterns."""
    return any(matches_family_pattern(api_name, p) for p in patterns)


def filter_instances(
    instances: list[dict],
    patterns: list[str] | None = None,
    cpu: Requirement | None = None,
    ram: Requirement | None = None,
    arches: list[str] | None = None,
    max_price: float | None = None,
    ebs_min: int | None = None,
    nvme: bool | None = None,
) -> list[dict]:
    """Filter instances by family patterns and cpu/ram/arch/price/ebs/nvme requirements.

    CPU and RAM use exact matching (or range if specified).
    """
    result = []
    for inst in instances:
        # check family pattern (skip if patterns is None)
        if patterns is not None and not matches_any_pattern(inst["api_name"], patterns):
            continue

        # check cpu requirement (exact or range match)
        if cpu is not None and not cpu.matches(inst["vcpus"]):
            continue

        # check ram requirement (exact or range match)
        if ram is not None and not ram.matches(inst["memory_gb"]):
            continue

        # check architecture
        if arches is not None and inst["arch"] not in arches:
            continue

        # check price budget
        if max_price is not None and (inst["price"] is None or inst["price"] >= max_price):
            continue

        # check EBS bandwidth minimum
        if ebs_min is not None and (inst["ebs_mbps"] is None or inst["ebs_mbps"] < ebs_min):
            continue

        # check local NVMe storage requirement
        if nvme is not None and inst.get("nvme", False) != nvme:
            continue

        result.append(inst)

    return result


def find_matching_runners(
    instance: dict,
    runner_configs: dict[str, dict],
) -> list[str]:
    """Find which runners would match this instance.

    A runner matches if:
    - Family pattern matches the instance
    - Instance cpu matches runner's requirement (exact or range)
    - Instance ram matches runner's requirement (exact or range)
    """
    matching = []
    for runner_name, config in runner_configs.items():
        # check family pattern
        if not matches_any_pattern(instance["api_name"], config["families"]):
            continue

        # check cpu requirement (exact or range); None means no constraint
        if config["cpu"] is not None and not config["cpu"].matches(instance["vcpus"]):
            continue

        # check ram requirement (exact or range); None means no constraint
        if config["ram"] is not None and not config["ram"].matches(instance["memory_gb"]):
            continue

        matching.append(runner_name)

    return matching


def get_runner_price_range(
    runner_config: dict, instances: list[dict]
) -> tuple[float | None, float | None, str | None, str | None]:
    """Get min/max price for a runner's family list.

    Returns (min_price, max_price, min_instance_name, max_instance_name).
    """
    families = runner_config.get("families", [])
    if not families:
        return None, None, None, None

    matching = []
    for inst in instances:
        api_name = inst["api_name"]
        if matches_any_pattern(api_name, families):
            # also check cpu/ram if specified
            cpu_req = runner_config.get("cpu")
            ram_req = runner_config.get("ram")

            if cpu_req is not None and not cpu_req.matches(inst["vcpus"]):
                continue
            if ram_req is not None and not ram_req.matches(inst["memory_gb"]):
                continue

            if inst.get("price") is not None:
                matching.append(inst)

    if not matching:
        return None, None, None, None

    matching.sort(key=lambda x: x["price"])
    cheapest = matching[0]
    most_expensive = matching[-1]

    return (
        cheapest["price"],
        most_expensive["price"],
        cheapest["api_name"],
        most_expensive["api_name"],
    )


def get_instance_price(instance_name: str, instances: list[dict]) -> float | None:
    """Look up exact instance price from loaded instances."""
    for inst in instances:
        if inst["api_name"] == instance_name:
            return inst.get("price")
    return None
