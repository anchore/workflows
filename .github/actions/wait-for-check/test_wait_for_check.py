"""Unit tests for wait_for_check.py"""

import pytest

from wait_for_check import (
    ValidationError,
    find_completed_check,
    parse_args,
    validate_config,
    write_output,
)


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_valid_config(self):
        config = validate_config(
            token="ghp_xxx",
            repository="owner/repo",
            check_name="Unit tests",
            ref="abc123",
            timeout_seconds=600,
            interval_seconds=30,
        )
        assert config.token == "ghp_xxx"
        assert config.repository == "owner/repo"
        assert config.check_name == "Unit tests"
        assert config.ref == "abc123"
        assert config.timeout_seconds == 600
        assert config.interval_seconds == 30

    def test_missing_token(self):
        with pytest.raises(ValidationError, match="token is required"):
            validate_config(
                token=None,
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_empty_token(self):
        with pytest.raises(ValidationError, match="token is required"):
            validate_config(
                token="",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_invalid_repository_format(self):
        with pytest.raises(ValidationError, match="owner/repo"):
            validate_config(
                token="token",
                repository="invalid",
                check_name="test",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_missing_check_name(self):
        with pytest.raises(ValidationError, match="check_name is required"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_invalid_timeout(self):
        with pytest.raises(ValidationError, match="timeout_seconds must be positive"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=0,
                interval_seconds=30,
            )

    def test_invalid_interval(self):
        with pytest.raises(ValidationError, match="interval_seconds must be positive"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=-1,
            )

    def test_interval_greater_than_timeout(self):
        with pytest.raises(ValidationError, match="interval_seconds.*must be less than"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=30,
                interval_seconds=60,
            )

    def test_multiple_errors(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_config(
                token="",
                repository="",
                check_name="",
                ref="",
                timeout_seconds=0,
                interval_seconds=0,
            )
        error_msg = str(exc_info.value)
        assert "token is required" in error_msg
        assert "repository is required" in error_msg

    def test_valid_ref_sha(self):
        """40-char hex SHA should be accepted."""
        config = validate_config(
            token="token",
            repository="owner/repo",
            check_name="test",
            ref="a" * 40,
            timeout_seconds=600,
            interval_seconds=30,
        )
        assert config.ref == "a" * 40

    def test_valid_ref_branch_name(self):
        """Standard branch names should be accepted."""
        config = validate_config(
            token="token",
            repository="owner/repo",
            check_name="test",
            ref="refs/heads/main",
            timeout_seconds=600,
            interval_seconds=30,
        )
        assert config.ref == "refs/heads/main"

    def test_valid_ref_with_dots_and_dashes(self):
        """Refs with dots and dashes should be accepted."""
        config = validate_config(
            token="token",
            repository="owner/repo",
            check_name="test",
            ref="feature/my-branch.1",
            timeout_seconds=600,
            interval_seconds=30,
        )
        assert config.ref == "feature/my-branch.1"

    def test_invalid_ref_with_shell_metachar(self):
        """Refs with shell metacharacters should be rejected."""
        with pytest.raises(ValidationError, match="ref contains invalid characters"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="$(whoami)",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_invalid_ref_with_backticks(self):
        """Refs with backticks should be rejected."""
        with pytest.raises(ValidationError, match="ref contains invalid characters"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="`id`",
                timeout_seconds=600,
                interval_seconds=30,
            )

    def test_invalid_ref_with_semicolon(self):
        """Refs with semicolons should be rejected."""
        with pytest.raises(ValidationError, match="ref contains invalid characters"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="main; rm -rf /",
                timeout_seconds=600,
                interval_seconds=30,
            )


class TestFindCompletedCheck:
    """Tests for find_completed_check function."""

    def test_finds_completed_check(self):
        check_runs = [
            {"name": "Unit tests", "status": "completed", "conclusion": "success"},
            {"name": "Lint", "status": "in_progress", "conclusion": None},
        ]
        result = find_completed_check(check_runs, "Unit tests")
        assert result == "success"

    def test_returns_none_for_in_progress(self):
        check_runs = [
            {"name": "Unit tests", "status": "in_progress", "conclusion": None},
        ]
        result = find_completed_check(check_runs, "Unit tests")
        assert result is None

    def test_returns_none_for_missing_check(self):
        check_runs = [
            {"name": "Other check", "status": "completed", "conclusion": "success"},
        ]
        result = find_completed_check(check_runs, "Unit tests")
        assert result is None

    def test_handles_empty_list(self):
        result = find_completed_check([], "Unit tests")
        assert result is None

    def test_handles_failure_conclusion(self):
        check_runs = [
            {"name": "Unit tests", "status": "completed", "conclusion": "failure"},
        ]
        result = find_completed_check(check_runs, "Unit tests")
        assert result == "failure"

    def test_exact_name_match(self):
        """Ensure we don't do partial matching."""
        check_runs = [
            {"name": "Unit tests (fast)", "status": "completed", "conclusion": "success"},
            {"name": "Unit tests", "status": "in_progress", "conclusion": None},
        ]
        result = find_completed_check(check_runs, "Unit tests")
        assert result is None


class TestParseArgs:
    """Tests for argument parsing."""

    def test_all_args(self):
        args = parse_args(
            [
                "--token",
                "ghp_xxx",
                "--repository",
                "owner/repo",
                "--check-name",
                "Unit tests",
                "--ref",
                "abc123",
                "--timeout-seconds",
                "300",
                "--interval-seconds",
                "15",
            ]
        )
        assert args.token == "ghp_xxx"
        assert args.repository == "owner/repo"
        assert args.check_name == "Unit tests"
        assert args.ref == "abc123"
        assert args.timeout_seconds == 300
        assert args.interval_seconds == 15

    def test_defaults(self):
        args = parse_args(
            [
                "--token",
                "ghp_xxx",
                "--repository",
                "owner/repo",
                "--check-name",
                "test",
                "--ref",
                "abc123",
            ]
        )
        assert args.timeout_seconds == 600
        assert args.interval_seconds == 30

    def test_missing_required_arg(self):
        with pytest.raises(SystemExit):
            parse_args(["--token", "ghp_xxx"])


class TestWriteOutput:
    """Tests for write_output function."""

    def test_sanitizes_newlines(self, tmp_path, monkeypatch):
        """Newlines in values should be replaced with spaces."""
        output_file = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        write_output("test", "line1\nline2\nline3")

        content = output_file.read_text()
        assert content == "test=line1 line2 line3\n"

    def test_sanitizes_carriage_returns(self, tmp_path, monkeypatch):
        """Carriage returns should be removed."""
        output_file = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        write_output("test", "line1\r\nline2")

        content = output_file.read_text()
        assert content == "test=line1 line2\n"

    def test_prevents_output_injection(self, tmp_path, monkeypatch):
        """Malicious values with newlines should not inject extra outputs."""
        output_file = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        # attempt to inject an extra output via newline
        write_output("conclusion", "success\nmalicious=injected")

        content = output_file.read_text()
        # should be a single line, with the injection attempt neutralized
        assert content == "conclusion=success malicious=injected\n"
        assert content.count("\n") == 1

    def test_normal_value_unchanged(self, tmp_path, monkeypatch):
        """Normal values without special chars should be written as-is."""
        output_file = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

        write_output("conclusion", "success")

        content = output_file.read_text()
        assert content == "conclusion=success\n"
