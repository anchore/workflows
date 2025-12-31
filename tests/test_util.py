"""Tests for runson.cli.util module."""

from __future__ import annotations

import pytest

from runson.cli.util import (
    format_duration,
    format_range,
    parse_ebs_bandwidth,
    parse_hourly_cost,
    parse_memory_gb,
    parse_vcpus,
)


class TestParseVcpus:
    """Tests for parse_vcpus function."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("8 vCPUs", 8),
            ("16 vCPUs", 16),
            ("2 vCPUs", 2),
            ("192 vCPUs", 192),
            ("1 vCPU", 1),
            ("4vCPUs", 4),  # no space
            ("8 VCPUS", 8),  # uppercase
        ],
    )
    def test_valid_inputs(self, input_str: str, expected: int):
        assert parse_vcpus(input_str) == expected

    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            None,
            "no cpus here",
            "8 cores",
        ],
    )
    def test_invalid_inputs(self, input_str: str | None):
        assert parse_vcpus(input_str) is None


class TestParseMemoryGb:
    """Tests for parse_memory_gb function."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("32 GiB", 32.0),
            ("16 GiB", 16.0),
            ("8 GB", 8.0),
            ("2048 GiB", 2048.0),
            ("0.5 GiB", 0.5),
            ("16GiB", 16.0),  # no space
            ("32 gib", 32.0),  # lowercase
        ],
    )
    def test_valid_inputs(self, input_str: str, expected: float):
        assert parse_memory_gb(input_str) == expected

    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            None,
            "no memory here",
            "32 MiB",
        ],
    )
    def test_invalid_inputs(self, input_str: str | None):
        assert parse_memory_gb(input_str) is None


class TestParseHourlyCost:
    """Tests for parse_hourly_cost function."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("$0.204 hourly", 0.204),
            ("$1.50 hourly", 1.50),
            ("$98.32 hourly", 98.32),
            ("$0.008 hourly", 0.008),
            ("$0.204 Hourly", 0.204),  # mixed case
        ],
    )
    def test_valid_inputs(self, input_str: str, expected: float):
        assert parse_hourly_cost(input_str) == expected

    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            None,
            "0.204 hourly",  # missing $
            "$0.204",  # missing hourly
        ],
    )
    def test_invalid_inputs(self, input_str: str | None):
        assert parse_hourly_cost(input_str) is None


class TestParseEbsBandwidth:
    """Tests for parse_ebs_bandwidth function."""

    @pytest.mark.parametrize(
        "input_str,expected",
        [
            ("10000 Mbps", 10000),
            ("5000 Mbps", 5000),
            ("80000 Mbps", 80000),
            ("10000Mbps", 10000),  # no space
            ("10000 mbps", 10000),  # lowercase
        ],
    )
    def test_valid_inputs(self, input_str: str, expected: int):
        assert parse_ebs_bandwidth(input_str) == expected

    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            None,
            "10 Gbps",  # wrong unit
            "no bandwidth",
        ],
    )
    def test_invalid_inputs(self, input_str: str | None):
        assert parse_ebs_bandwidth(input_str) is None


class TestFormatDuration:
    """Tests for format_duration function."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (30, "30s"),
            (59.9, "60s"),
            (60, "1m 00s"),
            (90, "1m 30s"),
            (3599, "59m 59s"),
            (3600, "1h 00m"),
            (3660, "1h 01m"),
            (7200, "2h 00m"),
        ],
    )
    def test_format_duration(self, seconds: float, expected: str):
        assert format_duration(seconds) == expected


class TestFormatRange:
    """Tests for format_range function."""

    @pytest.mark.parametrize(
        "values,suffix,expected",
        [
            ([1, 2, 3], "", "1-3"),
            ([5, 5, 5], "", "5"),
            ([1, 10], " vCPUs", "1-10 vCPUs"),
            ([8], " GiB", "8 GiB"),
            ([], "", "N/A"),
            ([3, 1, 2], "", "1-3"),  # unsorted
        ],
    )
    def test_format_range(self, values: list, suffix: str, expected: str):
        assert format_range(values, suffix) == expected
