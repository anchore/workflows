"""Unit tests for wait_for_check.py"""

import json
from urllib.error import URLError

import pytest

from wait_for_check import (
    MAX_PAGES,
    PER_PAGE,
    Config,
    ValidationError,
    fetch_check_runs,
    find_matching_checks,
    parse_args,
    resolve_conclusion,
    validate_config,
    wait_for_check,
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

    def test_invalid_not_found_timeout(self):
        with pytest.raises(ValidationError, match="not_found_timeout_seconds must be positive"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=600,
                interval_seconds=30,
                not_found_timeout_seconds=0,
            )

    def test_not_found_timeout_not_less_than_timeout(self):
        with pytest.raises(ValidationError, match="not_found_timeout_seconds.*must be less than"):
            validate_config(
                token="token",
                repository="owner/repo",
                check_name="test",
                ref="abc123",
                timeout_seconds=60,
                interval_seconds=30,
                not_found_timeout_seconds=60,
            )

    def test_not_found_timeout_and_verbose_defaults(self):
        config = validate_config(
            token="token",
            repository="owner/repo",
            check_name="test",
            ref="abc123",
            timeout_seconds=600,
            interval_seconds=30,
        )
        assert config.not_found_timeout_seconds == 60
        assert config.verbose is False

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


class TestFindMatchingChecks:
    """Tests for find_matching_checks function."""

    def test_finds_matching_check(self):
        check_runs = [
            {"name": "Unit tests", "status": "completed", "conclusion": "success"},
            {"name": "Lint", "status": "in_progress", "conclusion": None},
        ]
        result = find_matching_checks(check_runs, "Unit tests")
        assert result == [check_runs[0]]

    def test_returns_empty_for_missing_check(self):
        check_runs = [
            {"name": "Other check", "status": "completed", "conclusion": "success"},
        ]
        assert find_matching_checks(check_runs, "Unit tests") == []

    def test_handles_empty_list(self):
        assert find_matching_checks([], "Unit tests") == []

    def test_case_insensitive_match(self):
        """Names that differ only by case should still match."""
        check_runs = [
            {"name": "Static Analysis", "status": "completed", "conclusion": "success"},
        ]
        assert find_matching_checks(check_runs, "static analysis") == check_runs
        assert find_matching_checks(check_runs, "STATIC ANALYSIS") == check_runs

    def test_whitespace_trimmed_match(self):
        """Leading/trailing whitespace should not prevent a match."""
        check_runs = [
            {"name": "  Unit tests  ", "status": "completed", "conclusion": "success"},
        ]
        assert find_matching_checks(check_runs, "Unit tests") == check_runs

    def test_no_partial_match(self):
        """Ensure we don't do partial/substring matching."""
        check_runs = [
            {"name": "Unit tests (fast)", "status": "completed", "conclusion": "success"},
        ]
        assert find_matching_checks(check_runs, "Unit tests") == []

    def test_returns_all_duplicates(self):
        """All check runs sharing a name should be returned."""
        check_runs = [
            {"name": "Build", "status": "completed", "conclusion": "success"},
            {"name": "build", "status": "completed", "conclusion": "failure"},
        ]
        assert find_matching_checks(check_runs, "Build") == check_runs


class TestResolveConclusion:
    """Tests for resolve_conclusion function."""

    def test_empty_returns_none(self):
        """No matching runs must never resolve to success."""
        assert resolve_conclusion([]) is None

    def test_single_success(self):
        matches = [{"name": "x", "status": "completed", "conclusion": "success"}]
        assert resolve_conclusion(matches) == "success"

    def test_single_failure(self):
        matches = [{"name": "x", "status": "completed", "conclusion": "failure"}]
        assert resolve_conclusion(matches) == "failure"

    def test_pending_returns_none(self):
        matches = [{"name": "x", "status": "in_progress", "conclusion": None}]
        assert resolve_conclusion(matches) is None

    def test_pending_when_any_incomplete(self):
        """Wait for the full set: one pending run keeps the verdict open."""
        matches = [
            {"name": "x", "status": "completed", "conclusion": "success"},
            {"name": "x", "status": "in_progress", "conclusion": None},
        ]
        assert resolve_conclusion(matches) is None

    def test_duplicates_all_success(self):
        matches = [
            {"name": "x", "status": "completed", "conclusion": "success"},
            {"name": "x", "status": "completed", "conclusion": "success"},
        ]
        assert resolve_conclusion(matches) == "success"

    def test_duplicates_fail_closed(self):
        """If any matching run failed, the result is non-success."""
        matches = [
            {"name": "x", "status": "completed", "conclusion": "success"},
            {"name": "x", "status": "completed", "conclusion": "failure"},
        ]
        assert resolve_conclusion(matches) == "failure"

    def test_completed_without_conclusion_treated_as_failure(self):
        matches = [{"name": "x", "status": "completed", "conclusion": None}]
        assert resolve_conclusion(matches) == "failure"


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
                "--not-found-timeout-seconds",
                "90",
                "--verbose",
            ]
        )
        assert args.token == "ghp_xxx"
        assert args.repository == "owner/repo"
        assert args.check_name == "Unit tests"
        assert args.ref == "abc123"
        assert args.timeout_seconds == 300
        assert args.interval_seconds == 15
        assert args.not_found_timeout_seconds == 90
        assert args.verbose is True

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
        assert args.not_found_timeout_seconds == 60
        assert args.verbose is False

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


class _FakeResponse:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_fake_urlopen(pages, calls):
    """Build a urlopen replacement that serves `pages` in order and records URLs."""

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        calls.append(request.full_url)
        return _FakeResponse(pages[len(calls) - 1])

    return fake_urlopen


class TestFetchCheckRuns:
    """Tests for fetch_check_runs pagination."""

    def test_single_page(self, monkeypatch):
        pages = [{"total_count": 2, "check_runs": [{"name": "a"}, {"name": "b"}]}]
        calls: list[str] = []
        monkeypatch.setattr("wait_for_check.urlopen", _make_fake_urlopen(pages, calls))

        runs = fetch_check_runs("token", "owner/repo", "abc123")

        assert [r["name"] for r in runs] == ["a", "b"]
        assert len(calls) == 1

    def test_follows_pagination(self, monkeypatch):
        page1 = {"total_count": 150, "check_runs": [{"name": f"c{i}"} for i in range(PER_PAGE)]}
        page2 = {"total_count": 150, "check_runs": [{"name": f"c{i}"} for i in range(PER_PAGE, 150)]}
        calls: list[str] = []
        monkeypatch.setattr("wait_for_check.urlopen", _make_fake_urlopen([page1, page2], calls))

        runs = fetch_check_runs("token", "owner/repo", "abc123")

        assert len(runs) == 150
        assert len(calls) == 2
        assert "page=2" in calls[1]

    def test_respects_max_pages_and_warns(self, monkeypatch, capsys):
        # every page is full and total_count never satisfied, so only the cap stops us
        full_page = {"total_count": 10_000, "check_runs": [{"name": "x"} for _ in range(PER_PAGE)]}
        calls: list[str] = []
        monkeypatch.setattr("wait_for_check.urlopen", _make_fake_urlopen([full_page] * MAX_PAGES, calls))

        runs = fetch_check_runs("token", "owner/repo", "abc123")

        assert len(calls) == MAX_PAGES
        assert len(runs) == MAX_PAGES * PER_PAGE
        assert "pagination cap" in capsys.readouterr().out


def _diag_config(**overrides):
    base = {
        "token": "token",
        "repository": "owner/repo",
        "check_name": "Build",
        "ref": "abc123",
        "timeout_seconds": 10,
        "interval_seconds": 1,
        "not_found_timeout_seconds": 3,
        "verbose": False,
    }
    base.update(overrides)
    return Config(**base)


def _freeze_clock(monkeypatch):
    """Make time.time advance only when time.sleep is called, so the loop runs fast."""
    clock = {"now": 1000.0}
    monkeypatch.setattr("wait_for_check.time.time", lambda: clock["now"])
    monkeypatch.setattr("wait_for_check.time.sleep", lambda s: clock.__setitem__("now", clock["now"] + s))
    return clock


class TestWaitForCheckDiagnostics:
    """Tests for the not-found / never-seen reporting in wait_for_check."""

    def test_persistent_api_errors_reported_as_connectivity(self, monkeypatch, capsys):
        """When every poll fails, blame the API - not a name mismatch."""
        _freeze_clock(monkeypatch)

        def boom(*args, **kwargs):
            raise URLError("connection refused")

        monkeypatch.setattr("wait_for_check.fetch_check_runs", boom)

        result = wait_for_check(_diag_config())

        out = capsys.readouterr().out
        assert result == "not_found"
        assert "every GitHub API request failed" in out
        assert "does not match" not in out

    def test_unmatched_name_reports_available_checks(self, monkeypatch, capsys):
        """When polling succeeds but the name is absent, blame the name and list what was seen."""
        _freeze_clock(monkeypatch)
        monkeypatch.setattr(
            "wait_for_check.fetch_check_runs",
            lambda *a, **k: [{"name": "Other", "status": "completed", "conclusion": "success"}],
        )

        result = wait_for_check(_diag_config())

        out = capsys.readouterr().out
        assert result == "not_found"
        assert "does not match" in out
        assert "Other" in out

    def test_success_returns_conclusion(self, monkeypatch, capsys):
        _freeze_clock(monkeypatch)
        monkeypatch.setattr(
            "wait_for_check.fetch_check_runs",
            lambda *a, **k: [{"name": "build", "status": "completed", "conclusion": "success"}],
        )

        assert wait_for_check(_diag_config()) == "success"
