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


class TestFamilyCliGlobsWithFilters:
    """Tests for --globs flag with cpu/mem/budget filters."""

    def test_globs_stats_reflect_filtered_instances(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify glob stats (CPU, RAM ranges) only reflect filtered instances."""
        result = runner.invoke(
            family,
            ["m7*", "--cpu", "2", "--mem", "8", "--globs", "-c", str(runs_on_yml)],
        )

        assert result.exit_code == 0
        # output should show m7* glob with stats matching the filter
        assert "m7*" in result.output
        # stats should show "2 CPU" not "2-8 CPU" since we filtered to cpu=2
        assert "2 CPU" in result.output
        # stats should show "8GB" not "8-32GB" since we filtered to mem=8
        assert "8GB" in result.output
        # should NOT see larger CPU/RAM values in the stats
        assert "2-8 CPU" not in result.output
        assert "8-32GB" not in result.output

    def test_globs_with_budget_uses_filtered_universe(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify budget check uses filtered universe, allowing broader globs."""
        # filter to only 2 vCPU instances, then check if m7* glob is allowed
        # m7i.large=$0.096, m7a.large=$0.102, m7g.large=$0.082 - all under $0.15
        result = runner.invoke(
            family,
            ["m7*", "--cpu", "2", "--budget", "0.15", "--globs", "-c", str(runs_on_yml)],
        )

        assert result.exit_code == 0
        # with filtered universe (only 2 vCPU m7 instances), m7* should be under budget
        # before the fix, this would check against m7i.2xlarge ($0.384) and refine to exact names
        assert "m7*" in result.output

    def test_globs_price_range_reflects_filtered_instances(
        self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path
    ):
        """Verify price ranges in glob output only reflect filtered instances."""
        result = runner.invoke(
            family,
            ["m7*", "--cpu", "2", "--globs", "-c", str(runs_on_yml)],
        )

        assert result.exit_code == 0
        # m7 instances with 2 vCPUs: m7i.large=$0.096, m7a.large=$0.102, m7g.large=$0.082
        # price range should be ~$0.08-$0.10, not include $0.384 from m7i.2xlarge
        assert "$0.08" in result.output or "$0.09" in result.output or "$0.10" in result.output
        # should NOT show prices from larger instances
        assert "$0.38" not in result.output
        assert "$0.19" not in result.output

    def test_globs_with_cpu_mem_range_filters(self, runner: CliRunner, mock_csv_path: Path, runs_on_yml: Path):
        """Verify globs work correctly with CPU and RAM range filters."""
        result = runner.invoke(
            family,
            ["m7*", "--cpu", "2:4", "--mem", "8:16", "--globs", "-c", str(runs_on_yml)],
        )

        assert result.exit_code == 0
        assert "m7*" in result.output
        # stats should show the filtered range
        assert "2-4 CPU" in result.output
        assert "8-16GB" in result.output
