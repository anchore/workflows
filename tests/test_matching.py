"""Tests for runson.core.matching module."""

from __future__ import annotations

import pytest

from runson.cli.config import Requirement
from runson.core.matching import (
    filter_instances,
    find_matching_runners,
    get_instance_price,
    get_runner_price_range,
    matches_any_pattern,
    matches_family_pattern,
)


class TestMatchesFamilyPattern:
    """Tests for matches_family_pattern function."""

    @pytest.mark.parametrize(
        "api_name,pattern,expected",
        [
            # prefix pattern matches (2-char)
            ("m7i.large", "m7", True),
            ("m7a.xlarge", "m7", True),
            ("m7g.2xlarge", "m7", True),
            ("m6i.large", "m7", False),
            # variant pattern matches (3-char)
            ("m7i.large", "m7i", True),
            ("m7i.xlarge", "m7i", True),
            ("m7a.large", "m7i", False),
            ("m7g.large", "m7i", False),
            # with trailing wildcard
            ("m7i.large", "m7*", True),
            ("m7i.large", "m7i*", True),
            # exact match
            ("m7i.large", "m7i.large", True),
            ("m7i.xlarge", "m7i.large", False),
            # case insensitivity
            ("M7I.large", "m7i", True),
            ("m7i.large", "M7I", True),
        ],
    )
    def test_pattern_matching(self, api_name: str, pattern: str, expected: bool):
        assert matches_family_pattern(api_name, pattern) == expected

    def test_empty_api_name(self):
        assert matches_family_pattern("", "m7") is False

    def test_none_api_name(self):
        # the function should handle None gracefully
        assert matches_family_pattern(None, "m7") is False


class TestMatchesAnyPattern:
    """Tests for matches_any_pattern function."""

    def test_matches_first_pattern(self):
        assert matches_any_pattern("m7i.large", ["m7", "c7"]) is True

    def test_matches_second_pattern(self):
        assert matches_any_pattern("c7i.large", ["m7", "c7"]) is True

    def test_matches_none(self):
        assert matches_any_pattern("r7i.large", ["m7", "c7"]) is False

    def test_empty_patterns(self):
        assert matches_any_pattern("m7i.large", []) is False


class TestFilterInstances:
    """Tests for filter_instances function."""

    def test_filter_by_pattern(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, patterns=["m7*"])

        api_names = [i["api_name"] for i in result]
        assert "m7i.large" in api_names
        assert "m7a.large" in api_names
        assert "m7g.large" in api_names
        assert "c7i.large" not in api_names

    def test_filter_by_cpu_exact(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, cpu=Requirement(min_val=4))

        for inst in result:
            assert inst["vcpus"] == 4

    def test_filter_by_cpu_range(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, cpu=Requirement(min_val=2, max_val=4))

        for inst in result:
            assert 2 <= inst["vcpus"] <= 4

    def test_filter_by_ram_exact(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, ram=Requirement(min_val=16.0))

        for inst in result:
            assert inst["memory_gb"] == 16.0

    def test_filter_by_arch(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, arches=["arm64"])

        for inst in result:
            assert inst["arch"] == "arm64"

        api_names = [i["api_name"] for i in result]
        assert "m7g.large" in api_names
        assert "t4g.small" in api_names
        assert "a1.medium" in api_names

    def test_filter_by_max_price(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, max_price=0.10)

        for inst in result:
            assert inst["price"] < 0.10

    def test_filter_by_ebs_min(self, sample_instances: list[dict]):
        result = filter_instances(sample_instances, ebs_min=50000)

        for inst in result:
            assert inst["ebs_mbps"] is not None
            assert inst["ebs_mbps"] >= 50000

        # only p5.48xlarge has 80000 Mbps
        assert len(result) == 1
        assert result[0]["api_name"] == "p5.48xlarge"

    def test_combined_filters(self, sample_instances: list[dict]):
        result = filter_instances(
            sample_instances,
            patterns=["m7*"],
            cpu=Requirement(min_val=2, max_val=4),
            arches=["x86_64"],
        )

        for inst in result:
            assert inst["api_name"].startswith("m7i")
            assert 2 <= inst["vcpus"] <= 4
            assert inst["arch"] == "x86_64"


class TestFindMatchingRunners:
    """Tests for find_matching_runners function."""

    def test_finds_matching_runner(self, sample_instances: list[dict]):
        runner_configs = {
            "general-4": {
                "families": ["m7*"],
                "cpu": Requirement(min_val=4),
                "ram": None,
            },
        }

        m7i_xlarge = next(i for i in sample_instances if i["api_name"] == "m7i.xlarge")
        result = find_matching_runners(m7i_xlarge, runner_configs)

        assert "general-4" in result

    def test_no_match_wrong_family(self, sample_instances: list[dict]):
        runner_configs = {
            "compute": {
                "families": ["c7*"],
                "cpu": None,
                "ram": None,
            },
        }

        m7i = next(i for i in sample_instances if i["api_name"] == "m7i.large")
        result = find_matching_runners(m7i, runner_configs)

        assert "compute" not in result

    def test_no_match_wrong_cpu(self, sample_instances: list[dict]):
        runner_configs = {
            "general-8": {
                "families": ["m7*"],
                "cpu": Requirement(min_val=8),
                "ram": None,
            },
        }

        m7i_large = next(i for i in sample_instances if i["api_name"] == "m7i.large")
        result = find_matching_runners(m7i_large, runner_configs)

        assert "general-8" not in result


class TestGetRunnerPriceRange:
    """Tests for get_runner_price_range function."""

    def test_returns_price_range(self, sample_instances: list[dict]):
        runner_config = {
            "families": ["m7i*"],
            "cpu": None,
            "ram": None,
        }

        min_price, max_price, min_name, max_name = get_runner_price_range(runner_config, sample_instances)

        assert min_price is not None
        assert max_price is not None
        assert min_price <= max_price
        assert min_name == "m7i.large"
        assert max_name == "m7i.2xlarge"

    def test_returns_none_for_no_matches(self, sample_instances: list[dict]):
        runner_config = {
            "families": ["x9*"],
            "cpu": None,
            "ram": None,
        }

        result = get_runner_price_range(runner_config, sample_instances)
        assert result == (None, None, None, None)

    def test_respects_cpu_filter(self, sample_instances: list[dict]):
        runner_config = {
            "families": ["m7i*"],
            "cpu": Requirement(min_val=4),
            "ram": None,
        }

        min_price, max_price, min_name, max_name = get_runner_price_range(runner_config, sample_instances)

        # should exclude m7i.large (2 vCPUs)
        assert min_name == "m7i.xlarge"


class TestGetInstancePrice:
    """Tests for get_instance_price function."""

    def test_finds_price(self, sample_instances: list[dict]):
        price = get_instance_price("m7i.large", sample_instances)
        assert price == 0.096

    def test_returns_none_for_unknown(self, sample_instances: list[dict]):
        price = get_instance_price("unknown.instance", sample_instances)
        assert price is None
