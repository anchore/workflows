"""Tests for runson.cli.config module."""

from __future__ import annotations

from pathlib import Path

import pytest

from runson.cli.config import (
    Requirement,
    find_repo_root,
    load_instances,
    parse_cli_requirement,
    parse_requirement,
    parse_runner_configs_from_data,
)


class TestRequirement:
    """Tests for Requirement dataclass."""

    def test_exact_match(self):
        req = Requirement(min_val=8)
        assert req.matches(8) is True
        assert req.matches(7) is False
        assert req.matches(9) is False

    def test_range_match(self):
        req = Requirement(min_val=8, max_val=16)
        assert req.matches(8) is True
        assert req.matches(12) is True
        assert req.matches(16) is True
        assert req.matches(7) is False
        assert req.matches(17) is False

    def test_float_range(self):
        req = Requirement(min_val=8.0, max_val=16.0)
        assert req.matches(8.0) is True
        assert req.matches(12.5) is True
        assert req.matches(16.0) is True

    def test_str_exact(self):
        req = Requirement(min_val=8)
        assert str(req) == "8"

    def test_str_range(self):
        req = Requirement(min_val=8, max_val=16)
        assert str(req) == "8:16"


class TestParseRequirement:
    """Tests for parse_requirement function."""

    @pytest.mark.parametrize(
        "value,expected_min,expected_max",
        [
            (8, 8, None),
            (16.5, 16.5, None),
            ([8], 8, None),
            ([8, 16], 8, 16),
        ],
    )
    def test_valid_inputs(self, value, expected_min, expected_max):
        req = parse_requirement(value)
        assert req is not None
        assert req.min_val == expected_min
        assert req.max_val == expected_max

    @pytest.mark.parametrize(
        "value",
        [
            None,
            [],
            [1, 2, 3],  # too many elements
            "invalid",
        ],
    )
    def test_invalid_inputs(self, value):
        assert parse_requirement(value) is None


class TestParseCliRequirement:
    """Tests for parse_cli_requirement function."""

    @pytest.mark.parametrize(
        "value,expected_min,expected_max",
        [
            ("8", 8, None),
            ("16", 16, None),
            ("8:16", 8, 16),
            (" 8 : 16 ", 8, 16),  # with spaces
        ],
    )
    def test_valid_inputs(self, value: str, expected_min: int, expected_max: int | None):
        req = parse_cli_requirement(value)
        assert req.min_val == expected_min
        assert req.max_val == expected_max

    def test_invalid_range_format_empty_min(self):
        with pytest.raises(ValueError, match="Invalid range format"):
            parse_cli_requirement(":16")

    def test_invalid_range_format_empty_max(self):
        with pytest.raises(ValueError, match="Invalid range format"):
            parse_cli_requirement("8:")

    def test_invalid_integer(self):
        with pytest.raises(ValueError, match="Invalid integer"):
            parse_cli_requirement("abc")


class TestLoadInstances:
    """Tests for load_instances function."""

    def test_loads_from_path(self, sample_csv_path: Path):
        instances = load_instances(sample_csv_path)

        assert len(instances) == 15

        # verify first instance
        m7i_large = next(i for i in instances if i["api_name"] == "m7i.large")
        assert m7i_large["vcpus"] == 2
        assert m7i_large["memory_gb"] == 8.0
        assert m7i_large["price"] == 0.096
        assert m7i_large["spot"] == 0.038
        assert m7i_large["arch"] == "x86_64"
        assert m7i_large["ebs_mbps"] == 10000

    def test_infers_architectures(self, sample_csv_path: Path):
        instances = load_instances(sample_csv_path)

        # Intel instances
        m7i = next(i for i in instances if i["api_name"] == "m7i.large")
        assert m7i["arch"] == "x86_64"

        # AMD instances
        m7a = next(i for i in instances if i["api_name"] == "m7a.large")
        assert m7a["arch"] == "amd64"

        # Graviton instances
        m7g = next(i for i in instances if i["api_name"] == "m7g.large")
        assert m7g["arch"] == "arm64"

        t4g = next(i for i in instances if i["api_name"] == "t4g.small")
        assert t4g["arch"] == "arm64"

        a1 = next(i for i in instances if i["api_name"] == "a1.medium")
        assert a1["arch"] == "arm64"

    def test_handles_missing_ebs(self, sample_csv_path: Path):
        instances = load_instances(sample_csv_path)

        # t4g.small has no EBS bandwidth in fixture
        t4g = next(i for i in instances if i["api_name"] == "t4g.small")
        assert t4g["ebs_mbps"] is None


class TestFindRepoRoot:
    """Tests for find_repo_root function."""

    def test_finds_github_dir(self, tmp_path: Path):
        # create a .github directory
        github_dir = tmp_path / "project" / ".github"
        github_dir.mkdir(parents=True)

        subdir = tmp_path / "project" / "src" / "pkg"
        subdir.mkdir(parents=True)

        result = find_repo_root(subdir)
        assert result == tmp_path / "project"

    def test_returns_start_if_no_github(self, tmp_path: Path):
        subdir = tmp_path / "project" / "src"
        subdir.mkdir(parents=True)

        result = find_repo_root(subdir)
        assert result == subdir


class TestParseRunnerConfigsFromData:
    """Tests for parse_runner_configs_from_data function."""

    def test_parses_basic_config(self):
        data = {
            "runners": {
                "default": {
                    "family": ["m7*", "c7*"],
                    "cpu": 8,
                    "ram": 16,
                },
            }
        }

        configs = parse_runner_configs_from_data(data)

        assert "default" in configs
        assert configs["default"]["families"] == ["m7*", "c7*"]
        assert configs["default"]["cpu"].min_val == 8
        assert configs["default"]["ram"].min_val == 16

    def test_parses_range_requirements(self):
        data = {
            "runners": {
                "flex": {
                    "family": ["m7*"],
                    "cpu": [4, 16],
                    "ram": [8, 32],
                },
            }
        }

        configs = parse_runner_configs_from_data(data)

        assert configs["flex"]["cpu"].min_val == 4
        assert configs["flex"]["cpu"].max_val == 16
        assert configs["flex"]["ram"].min_val == 8
        assert configs["flex"]["ram"].max_val == 32

    def test_includes_spot_setting(self):
        data = {
            "runners": {
                "on-demand": {
                    "family": ["m7*"],
                    "spot": False,
                },
            }
        }

        configs = parse_runner_configs_from_data(data)

        assert configs["on-demand"]["spot"] is False

    def test_excludes_empty_runners(self):
        data = {
            "runners": {
                "empty": {},
            }
        }

        configs = parse_runner_configs_from_data(data)

        assert "empty" not in configs
