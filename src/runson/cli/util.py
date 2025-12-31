"""Shared CLI utilities for terminal output and parsing."""

from __future__ import annotations

import os
import re
import sys


class C:
    """Terminal colors using ANSI escape codes."""

    _enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    BOLD = "\033[1m" if _enabled else ""
    DIM = "\033[2m" if _enabled else ""
    RED = "\033[91m" if _enabled else ""
    GREEN = "\033[92m" if _enabled else ""
    YELLOW = "\033[93m" if _enabled else ""
    CYAN = "\033[96m" if _enabled else ""
    WHITE = "\033[97m" if _enabled else ""
    RESET = "\033[0m" if _enabled else ""

    @classmethod
    def bold(cls, s: str) -> str:
        return f"{cls.BOLD}{s}{cls.RESET}"

    @classmethod
    def dim(cls, s: str) -> str:
        return f"{cls.DIM}{s}{cls.RESET}"

    @classmethod
    def green(cls, s: str) -> str:
        return f"{cls.GREEN}{s}{cls.RESET}"

    @classmethod
    def red(cls, s: str) -> str:
        return f"{cls.RED}{s}{cls.RESET}"

    @classmethod
    def yellow(cls, s: str) -> str:
        return f"{cls.YELLOW}{s}{cls.RESET}"

    @classmethod
    def cyan(cls, s: str) -> str:
        return f"{cls.CYAN}{s}{cls.RESET}"


def parse_vcpus(s: str) -> int | None:
    """Parse vCPU count from string like '8 vCPUs'."""
    if not s:
        return None
    match = re.search(r"(\d+)\s*vCPUs?", s, re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_memory_gb(s: str) -> float | None:
    """Parse memory in GB from string like '32 GiB' or '16 GB'."""
    if not s:
        return None
    match = re.search(r"([\d.]+)\s*(?:GiB|GB)", s, re.IGNORECASE)
    return float(match.group(1)) if match else None


def parse_hourly_cost(s: str) -> float | None:
    """Parse hourly cost from string like '$0.204 hourly'."""
    if not s:
        return None
    match = re.search(r"\$([\d.]+)\s*hourly", s, re.IGNORECASE)
    return float(match.group(1)) if match else None


def parse_ebs_bandwidth(s: str) -> int | None:
    """Parse EBS bandwidth from string like '10000 Mbps'."""
    if not s:
        return None
    match = re.search(r"(\d+)\s*Mbps", s, re.IGNORECASE)
    return int(match.group(1)) if match else None


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins:02d}m"


def format_range(values: list, suffix: str = "") -> str:
    """Format a list of values as a range string."""
    if not values:
        return "N/A"
    min_val, max_val = min(values), max(values)
    if min_val == max_val:
        return f"{min_val}{suffix}"
    return f"{min_val}-{max_val}{suffix}"
