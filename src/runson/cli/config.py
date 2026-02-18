"""Configuration loading and data access for runson CLI."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import yaml

from . import util

if TYPE_CHECKING:
    from importlib.abc import Traversable


# GitHub-hosted runner per-minute pricing
# https://docs.github.com/en/billing/reference/actions-runner-pricing

GITHUB_STANDARD_PRICING = {
    "linux": 0.008,  # $0.008/min for Linux 2-core
    "windows": 0.016,  # $0.016/min for Windows 2-core
    "macos": 0.08,  # $0.08/min for macOS
}

# larger runners pricing by (os, arch) -> {cores: per_minute_rate}
GITHUB_LARGER_RUNNER_PRICING = {
    ("linux", "x64"): {
        2: 0.008,
        4: 0.016,
        8: 0.032,
        16: 0.064,
        32: 0.128,
        64: 0.256,
        96: 0.384,
    },
    ("linux", "arm64"): {
        2: 0.005,
        4: 0.010,
        8: 0.020,
        16: 0.040,
        32: 0.080,
        64: 0.160,
    },
    ("windows", "x64"): {
        4: 0.032,
        8: 0.064,
        16: 0.128,
        32: 0.256,
        64: 0.512,
        96: 0.768,
    },
    ("windows", "arm64"): {
        2: 0.010,
        4: 0.020,
        8: 0.040,
        16: 0.080,
        32: 0.160,
        64: 0.320,
    },
    ("macos", "x64"): {
        12: 0.120,
    },
    ("macos", "arm64"): {
        5: 0.160,  # M2 Pro
    },
}


@dataclass
class Requirement:
    """A cpu or ram requirement that can be exact or a range.

    - Single value (max_val=None): exact match only
    - Range (max_val set): match values between min_val and max_val inclusive
    """

    min_val: int | float
    max_val: int | float | None = None

    def matches(self, value: int | float) -> bool:
        """Check if a value matches this requirement."""
        if self.max_val is None:
            return value == self.min_val  # exact match
        return self.min_val <= value <= self.max_val  # range match

    def __str__(self) -> str:
        if self.max_val is None:
            return str(self.min_val)
        return f"{self.min_val}:{self.max_val}"


def parse_requirement(value) -> Requirement | None:
    """Parse a requirement from YAML config (int, float, or list)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Requirement(min_val=value)  # exact
    if isinstance(value, list):
        if len(value) == 1:
            return Requirement(min_val=value[0])  # exact
        if len(value) == 2:
            return Requirement(min_val=value[0], max_val=value[1])  # range
    return None


def parse_cli_requirement(value: str) -> Requirement:
    """Parse a requirement from CLI (e.g., '8' or '8:16')."""
    try:
        if ":" in value:
            parts = value.split(":", 1)
            min_str, max_str = parts[0].strip(), parts[1].strip()
            if not min_str or not max_str:
                raise ValueError(f"Invalid range format: '{value}' (use 'min:max')")
            return Requirement(min_val=int(min_str), max_val=int(max_str))
        return Requirement(min_val=int(value))  # exact match
    except ValueError as e:
        if "invalid literal" in str(e):
            raise ValueError(f"Invalid integer in '{value}'") from e
        raise


def find_repo_root(start: Path) -> Path:
    """Find repository root by looking for .github directory."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".github").is_dir():
            return current
        current = current.parent
    return start


def get_data_dir() -> Traversable:
    """Get the package data directory using importlib.resources."""
    return files("runson.data")


def get_csv_path() -> Traversable:
    """Get path to the bundled AWS pricing CSV."""
    return get_data_dir().joinpath("amz-prices.csv")


def load_instances(csv_path: Path | Traversable | None = None) -> list[dict]:
    """Load and parse AWS instance data from CSV.

    If csv_path is None, uses the bundled package data.
    """
    if csv_path is None:
        csv_path = get_csv_path()

    instances = []

    # handle both Path and Traversable
    f: TextIO
    if isinstance(csv_path, Path):
        f = open(csv_path, newline="", encoding="utf-8")
    else:
        f = csv_path.open("r", encoding="utf-8")  # type: ignore[assignment]

    try:
        reader = csv.DictReader(f)
        for row in reader:
            api_name = row.get("API Name", "")
            vcpus = util.parse_vcpus(row.get("vCPUs", ""))
            memory_gb = util.parse_memory_gb(row.get("Instance Memory", ""))
            on_demand = util.parse_hourly_cost(row.get("On Demand", ""))
            spot = util.parse_hourly_cost(row.get("Linux Spot Minimum cost", ""))
            ebs_mbps = util.parse_ebs_bandwidth(row.get("EBS Optimized: Baseline Bandwidth", ""))
            instance_storage = row.get("Instance Storage", "")
            has_nvme = util.parse_has_local_nvme(instance_storage)
            local_storage_gb = util.parse_local_storage_gb(instance_storage)

            if api_name and vcpus is not None and memory_gb is not None:
                instances.append(
                    {
                        "api_name": api_name,
                        "vcpus": vcpus,
                        "memory_gb": memory_gb,
                        "price": on_demand,
                        "spot": spot,
                        "arch": _infer_arch(api_name),
                        "ebs_mbps": ebs_mbps,
                        "nvme": has_nvme,
                        "nvme_gb": local_storage_gb,
                    }
                )
    finally:
        f.close()

    return instances


def _infer_arch(api_name: str) -> str:
    """Infer CPU architecture from instance API name.

    This is a simplified version - the full implementation is in core.inference.
    Import is deferred to avoid circular imports.
    """
    # inline implementation to avoid circular import
    family = api_name.split(".")[0].lower() if "." in api_name else api_name.lower()

    if len(family) < 2:
        return "x86_64"

    suffix = family[2:]  # characters after generation number

    # Graviton/ARM instances
    if "g" in suffix or family.startswith("a1") or family.startswith("t4g"):
        return "arm64"

    # AMD instances
    if "a" in suffix:
        return "amd64"

    # Intel: everything else
    return "x86_64"


def parse_runner_configs_from_data(data: dict) -> dict[str, dict]:
    """Parse runner configs from loaded YAML data.

    Returns {runner_name: {"families": [...], "cpu": Requirement, "ram": Requirement}}.
    """
    runners = data.get("runners", {})
    result = {}

    for name, runner in runners.items():
        families_raw = runner.get("family", [])
        # keep patterns as-is (with wildcards) for matching
        families = [f.strip() for f in families_raw if f.strip()]

        # parse cpu/ram as Requirement (exact or range); None means no constraint
        cpu = parse_requirement(runner.get("cpu"))
        ram = parse_requirement(runner.get("ram"))

        # include runner if it has families OR cpu/ram requirements
        if families or cpu or ram:
            result[name] = {
                "families": families,
                "cpu": cpu,
                "ram": ram,
                "spot": runner.get("spot", True),
            }

    return result


def load_runner_configs(yaml_path: Path) -> dict[str, dict]:
    """Load full runner config from runs-on.yml.

    Returns {runner_name: {"families": [...], "cpu": Requirement, "ram": Requirement}}.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return parse_runner_configs_from_data(data)
