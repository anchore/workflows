"""Tests for runson family CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from runson.cli.family import family


@pytest.fixture
def runner() -> CliRunner:
    """Create a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def runs_on_yml(tmp_path: Path) -> Path:
    """Create a minimal runs-on.yml config file."""
    config = tmp_path / ".github" / "runs-on.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("""\
runners:
  general:
    family:
      - m7*
      - c7*
    cpu: 2:8
    ram: 8:32
  memory:
    family:
      - r7*
    cpu: 4
    ram: 32
""")
    return config


class TestFamilyCliCpuRamFilters:
    """Tests for --cpu and --mem filters in various modes."""

    def test_cpu_mem_applied_with_runner_configs(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify --cpu and --mem filters apply when using patterns from runs-on.yml.

        This tests the fix for a bug where CLI cpu/ram arguments were parsed
        but not applied when no selectors or --runner was specified.
        """
        result = runner.invoke(
            family,
            ["--cpu", "4", "--mem", "16", "-c", str(runs_on_yml), "-o", "yaml"],
        )

        assert result.exit_code == 0
        # should only show instances with exactly 4 vCPUs and 16GB RAM
        # from the sample data: m7i.xlarge, m7a.xlarge, m7g.xlarge, c7i.xlarge
        assert "m7i.xlarge" in result.output
        assert "m7g.xlarge" in result.output
        # should NOT show instances with 2 vCPUs or 8GB RAM
        assert "m7i.large" not in result.output
        assert "c7i.large" not in result.output

    def test_cpu_mem_range_applied_with_runner_configs(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify --cpu and --mem range filters apply when using patterns from runs-on.yml."""
        result = runner.invoke(
            family,
            ["--cpu", "2:4", "--mem", "8:16", "-c", str(runs_on_yml), "-o", "yaml"],
        )

        assert result.exit_code == 0
        # should show instances with 2-4 vCPUs and 8-16GB RAM
        assert "m7i.large" in result.output  # 2 vCPUs, 8GB
        assert "m7i.xlarge" in result.output  # 4 vCPUs, 16GB
        # should NOT show instances outside the range
        assert "m7i.2xlarge" not in result.output  # 8 vCPUs, 32GB

    def test_cpu_mem_applied_with_selectors(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify --cpu and --mem filters apply when using explicit selectors."""
        result = runner.invoke(
            family,
            ["m7*", "--cpu", "4", "--mem", "16", "-c", str(runs_on_yml), "-o", "yaml"],
        )

        assert result.exit_code == 0
        assert "m7i.xlarge" in result.output
        assert "m7i.large" not in result.output

    def test_cpu_mem_applied_with_runner_flag(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify --cpu and --mem from runner config apply with --runner flag."""
        result = runner.invoke(
            family,
            ["--runner", "memory", "-c", str(runs_on_yml), "-o", "yaml"],
        )

        assert result.exit_code == 0
        # memory runner: r7* family, cpu=4, ram=32
        assert "r7i.xlarge" in result.output  # 4 vCPUs, 32GB
        assert "r7i.large" not in result.output  # 2 vCPUs, 16GB

    def test_cli_cpu_mem_override_runner_with_pick_family(
        self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path
    ):
        """Verify CLI --cpu/--mem override runner defaults with --pick-family."""
        result = runner.invoke(
            family,
            [
                "--runner",
                "memory",
                "--pick-family",
                "--cpu",
                "2",
                "--mem",
                "16",
                "-c",
                str(runs_on_yml),
                "-o",
                "yaml",
            ],
        )

        assert result.exit_code == 0
        # pick-family ignores runner's family constraint, uses CLI cpu/ram
        # should show any family with 2 vCPUs and 16GB
        assert "r7i.large" in result.output  # 2 vCPUs, 16GB
        assert "r7a.large" in result.output  # 2 vCPUs, 16GB
        # should NOT show instances with 4 vCPUs or 32GB
        assert "r7i.xlarge" not in result.output

    def test_requirements_shown_in_output(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify requirements are displayed when using --cpu/--mem with runner configs."""
        result = runner.invoke(
            family,
            ["--cpu", "4:8", "--mem", "16:32", "-c", str(runs_on_yml)],
        )

        assert result.exit_code == 0
        # check that requirements are shown in the output
        assert "cpu=4:8" in result.output
        assert "ram=16:32GB" in result.output
