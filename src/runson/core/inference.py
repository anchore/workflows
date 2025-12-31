"""Instance type inference for architecture and category."""

from __future__ import annotations


def get_family_prefix(api_name: str) -> str:
    """Extract family prefix from instance API name.

    e.g., 'r7i.2xlarge' -> 'r7i', 'm6a.4xlarge' -> 'm6a'
    """
    if "." in api_name:
        return api_name.split(".")[0]
    return api_name


def infer_arch(api_name: str) -> str:
    """Infer CPU architecture from instance API name.

    AWS naming convention for processor suffix (after generation number):
    - 'g', 'gd', 'gn', 'gb' = Graviton (ARM) - e.g., r8g, m7gd
    - 'a', 'ad' = AMD - e.g., m6a, r7a
    - 'i', 'id', 'in' = Intel - e.g., m6i, r7i
    - no suffix or 'n', 'd' = Intel (older gens) - e.g., m5, c5d
    """
    family = get_family_prefix(api_name).lower()

    # need at least 2 chars to check generation-specific suffixes
    if len(family) < 2:
        return "x86_64"

    suffix = family[2:]  # characters after generation number

    # Graviton/ARM instances: contain 'g' after the generation number
    # e.g., r8g, m7g, r8gd, m7gn, t4g, a1
    if "g" in suffix or family.startswith("a1") or family.startswith("t4g"):
        return "arm64"

    # AMD instances: contain 'a' after the generation number (but not a1)
    # e.g., m6a, r7a, c6a, m6ad
    if "a" in suffix:
        return "amd64"

    # Intel: everything else (m6i, r7i, m5, c5, c5d, etc.)
    return "x86_64"


def get_instance_category(api_name: str) -> str:
    """Get the compute category for an instance type.

    Based on AWS instance family naming conventions:
    - c* = Compute optimized
    - m* = General purpose
    - r* = Memory optimized
    - t* = Burstable
    - i* = Storage optimized
    - etc.
    """
    family = get_family_prefix(api_name).lower()
    prefix = family[0]

    categories = {
        "c": "compute",
        "m": "general",
        "r": "memory",
        "t": "burstable",
        "i": "storage",
        "x": "memory",
        "z": "high-freq",
        "g": "gpu",
        "p": "gpu",
        "h": "storage",
        "d": "storage",
        "f": "fpga",
        "v": "video",
    }

    # special cases for multi-char prefixes
    if family.startswith("inf"):
        return "ml-inference"
    if family.startswith("a1"):
        return "general"

    return categories.get(prefix, "other")
