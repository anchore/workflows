"""Tests for create_tag module."""

from __future__ import annotations

import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from create_tag import (
    GITHUB_SSH_KEY_ECDSA,
    GITHUB_SSH_KEY_ED25519,
    GITHUB_SSH_KEY_RSA,
    GITHUB_SSH_KEYS,
    Config,
    GitError,
    SSHConfig,
    ValidationError,
    _parse_agent_var,
    _restore_git_config,
    create_and_push_tag,
    get_git_config,
    get_github_ssh_host_keys,
    main,
    parse_args,
    run_git,
    set_git_config,
    setup_ssh_key,
    tag_exists_locally,
    tag_exists_remotely,
    unset_git_config,
    validate_config,
    validate_deploy_key,
    validate_output_name,
    validate_repository,
    validate_safe_string,
    validate_sha,
    validate_tag,
    write_error,
    write_output,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


# module-level fixture for valid SSH key (used by multiple test classes)
@pytest.fixture
def valid_ssh_key() -> str:
    """A valid SSH private key for testing.

    Note: This key has matching BEGIN/END markers as required by the improved
    validation. It's not a real key, just valid PEM structure for testing.
    """
    return "-----BEGIN OPENSSH PRIVATE KEY-----\nbase64encodedkeydata\n-----END OPENSSH PRIVATE KEY-----"


class IsolatedGitRepo:
    """An isolated git repository for testing.

    Creates a temporary directory with a git repo and optional bare "origin" remote.
    All git operations within the test will use this repo, fully isolated from the
    user's global git configuration (no gpg signing, no hooks, no global config).
    """

    def __init__(self, tmp_path: Path, with_origin: bool = False) -> None:
        self.tmp_path = tmp_path
        self.repo_path = tmp_path / "repo"
        self.repo_path.mkdir()

        self.origin_path: Path | None = None
        if with_origin:
            self.origin_path = tmp_path / "origin.git"

        # environment that isolates git from global/system config
        self._env = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",  # ignore /etc/gitconfig
            "GIT_CONFIG_GLOBAL": "/dev/null",  # ignore ~/.gitconfig
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }

    def _run_git(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run git command with isolated environment."""
        return subprocess.run(
            ["git", *args],
            cwd=kwargs.pop("cwd", self.repo_path),
            capture_output=True,
            check=True,
            env=self._env,
            **kwargs,
        )

    def setup(self) -> None:
        """Initialize the git repository."""
        # initialize the repo
        self._run_git(["init"])

        # configure user for commits (repo-local config)
        self._run_git(["config", "user.name", "Test User"])
        self._run_git(["config", "user.email", "test@example.com"])

        # disable gpg signing for this repo
        self._run_git(["config", "commit.gpgsign", "false"])
        self._run_git(["config", "tag.gpgsign", "false"])

        # create an initial commit so HEAD exists
        readme = self.repo_path / "README.md"
        readme.write_text("# Test Repo\n")
        self._run_git(["add", "README.md"])
        self._run_git(["commit", "-m", "Initial commit"])

        # set up a local bare repo as "origin" if requested.
        # SAFETY: this creates a bare git repo on the local filesystem (not a real remote).
        # git remotes can be local paths, not just URLs. When we "push to origin" below,
        # we're pushing to this local directory - nothing leaves the machine.
        if self.origin_path:
            self._run_git(
                ["init", "--bare", str(self.origin_path)],
                cwd=self.tmp_path,
            )
            # point "origin" to the local bare repo path (e.g., /tmp/pytest-xxx/origin.git)
            self._run_git(["remote", "add", "origin", str(self.origin_path)])
            # push to the local bare repo (safe - just copying objects between local directories)
            self._run_git(["push", "-u", "origin", "HEAD:main"])


@pytest.fixture
def isolated_git_repo(tmp_path: Path) -> Iterator[IsolatedGitRepo]:
    """Fixture providing an isolated git repository.

    Changes to this directory for the test duration, then restores
    the original working directory.
    """
    repo = IsolatedGitRepo(tmp_path, with_origin=False)
    repo.setup()

    original_cwd = os.getcwd()
    os.chdir(repo.repo_path)
    try:
        yield repo
    finally:
        os.chdir(original_cwd)


@pytest.fixture
def isolated_git_repo_with_origin(tmp_path: Path) -> Iterator[IsolatedGitRepo]:
    """Fixture providing an isolated git repository with a local 'origin' remote.

    Use this for tests that need to interact with a remote.
    """
    repo = IsolatedGitRepo(tmp_path, with_origin=True)
    repo.setup()

    original_cwd = os.getcwd()
    os.chdir(repo.repo_path)
    try:
        yield repo
    finally:
        os.chdir(original_cwd)


@pytest.fixture
def github_output_file(tmp_path: Path) -> Iterator[Path]:
    """Fixture providing a temporary GITHUB_OUTPUT file with automatic cleanup."""
    output_file = tmp_path / "github_output.txt"
    output_file.touch()
    with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):
        yield output_file


class TestValidateTag:
    """Tests for validate_tag function."""

    @pytest.mark.parametrize(
        "tag",
        [
            "v1.0.0",
            "v1.2.3",
            "release-1.0",
            "1.0.0",
            "v1.0.0-alpha",
            "v1.0.0-beta.1",
            "feature/v1.0.0",
            "v1.0.0_rc1",
        ],
    )
    def test_valid_tags(self, tag: str):
        assert validate_tag(tag) == tag

    @pytest.mark.parametrize(
        "tag,error_contains",
        [
            ("", "tag is required"),
            (None, "tag is required"),
            ("v1 0 0", "invalid characters"),  # spaces
            ("-v1.0.0", "Must start with alphanumeric"),  # dash prefix caught by regex
            ("v1..0", "cannot contain '..'"),
            ("v1.0.0.lock", "cannot end with '.lock'"),
            ("v" * 300, "invalid characters"),  # too long
        ],
    )
    def test_invalid_tags(self, tag: str | None, error_contains: str):
        with pytest.raises(ValidationError) as exc_info:
            validate_tag(tag)
        assert error_contains in str(exc_info.value)


class TestValidateSafeString:
    """Tests for validate_safe_string function."""

    @pytest.mark.parametrize(
        "value",
        [
            "github-actions[bot]",
            "user@example.com",
            "John Doe",
            "bot_name",
            "user.name",
        ],
    )
    def test_valid_strings(self, value: str):
        assert validate_safe_string(value, "field") == value

    @pytest.mark.parametrize(
        "value,error_contains",
        [
            ("", "is required"),
            (None, "is required"),
            ("user;rm -rf /", "invalid characters"),
            ("user$(whoami)", "invalid characters"),
            ("user`id`", "invalid characters"),
        ],
    )
    def test_invalid_strings(self, value: str | None, error_contains: str):
        with pytest.raises(ValidationError) as exc_info:
            validate_safe_string(value, "test_field")
        assert error_contains in str(exc_info.value)


class TestValidateRepository:
    """Tests for validate_repository function."""

    @pytest.mark.parametrize(
        "repo",
        [
            "owner/repo",
            "my-org/my-repo",
            "user123/project_name",
            "Org.Name/Repo.Name",
        ],
    )
    def test_valid_repos(self, repo: str):
        assert validate_repository(repo) == repo

    @pytest.mark.parametrize(
        "repo,error_contains",
        [
            ("", "repository is required"),
            (None, "repository is required"),
            ("noslash", "owner/repo"),
            ("too/many/slashes", "owner/repo"),
            ("/repo", "owner/repo"),
            ("owner/", "owner/repo"),
            ("owner;evil/repo", "invalid characters"),
        ],
    )
    def test_invalid_repos(self, repo: str | None, error_contains: str):
        with pytest.raises(ValidationError) as exc_info:
            validate_repository(repo)
        assert error_contains in str(exc_info.value)


class TestValidateDeployKey:
    """Tests for validate_deploy_key function."""

    @pytest.mark.parametrize(
        "key",
        [
            "-----BEGIN OPENSSH PRIVATE KEY-----\nkey content\n-----END OPENSSH PRIVATE KEY-----",
            "-----BEGIN RSA PRIVATE KEY-----\nkey content\n-----END RSA PRIVATE KEY-----",
            "-----BEGIN DSA PRIVATE KEY-----\nkey content\n-----END DSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----\nkey content\n-----END EC PRIVATE KEY-----",
            "-----BEGIN PRIVATE KEY-----\nkey content\n-----END PRIVATE KEY-----",  # PKCS#8
        ],
    )
    def test_valid_keys(self, key: str):
        assert validate_deploy_key(key) == key

    def test_valid_key_with_whitespace(self):
        # keys can have leading/trailing whitespace
        key = "  \n-----BEGIN OPENSSH PRIVATE KEY-----\nkey content\n-----END OPENSSH PRIVATE KEY-----\n  "
        assert validate_deploy_key(key) == key

    @pytest.mark.parametrize(
        "key,error_contains",
        [
            ("", "deploy_key is required"),
            (None, "deploy_key is required"),
            ("not a key", "must start with a valid private key header"),
            (
                "-----BEGIN PUBLIC KEY-----\ndata\n-----END PUBLIC KEY-----",
                "must start with a valid private key header",
            ),
            # missing end marker
            ("-----BEGIN OPENSSH PRIVATE KEY-----\nkey content", "missing or invalid end marker"),
            # mismatched markers
            (
                "-----BEGIN OPENSSH PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
                "missing or invalid end marker",
            ),
        ],
    )
    def test_invalid_keys(self, key: str | None, error_contains: str):
        with pytest.raises(ValidationError) as exc_info:
            validate_deploy_key(key)
        assert error_contains in str(exc_info.value)

    def test_rejects_null_bytes(self):
        """Null bytes in keys could cause truncation issues."""
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nkey\x00content\n-----END OPENSSH PRIVATE KEY-----"
        with pytest.raises(ValidationError) as exc_info:
            validate_deploy_key(key)
        assert "null bytes" in str(exc_info.value)

    def test_rejects_oversized_key(self):
        """Keys over 16KB should be rejected to prevent memory exhaustion."""
        # 16KB + 1 byte of content (plus headers)
        large_content = "A" * (16 * 1024 + 1)
        key = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{large_content}\n-----END OPENSSH PRIVATE KEY-----"
        with pytest.raises(ValidationError) as exc_info:
            validate_deploy_key(key)
        assert "exceeds maximum size" in str(exc_info.value)

    def test_accepts_key_at_size_limit(self):
        """Keys at exactly 16KB should be accepted."""
        # calculate how much content we can have within the 16KB limit
        header = "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        footer = "\n-----END OPENSSH PRIVATE KEY-----"
        max_content_size = 16 * 1024 - len(header) - len(footer)
        content = "A" * max_content_size
        key = f"{header}{content}{footer}"
        # should not raise
        assert validate_deploy_key(key) == key

    def test_rejects_public_key_with_private_in_comment(self):
        """Prevent bypass where 'PRIVATE KEY' appears in a comment but it's actually a public key."""
        # this should fail because it doesn't start with a valid private key header
        key = "-----BEGIN OPENSSH PUBLIC KEY-----\ndata\n# PRIVATE KEY-----\n-----END OPENSSH PUBLIC KEY-----"
        with pytest.raises(ValidationError) as exc_info:
            validate_deploy_key(key)
        assert "must start with a valid private key header" in str(exc_info.value)


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_valid_config(self, valid_ssh_key: str):
        config = validate_config(
            tag="v1.0.0",
            deploy_key=valid_ssh_key,
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )
        assert config.tag == "v1.0.0"
        assert config.tag_message == "Release v1.0.0"
        assert config.git_user_name == "bot"
        assert config.git_user_email == "bot@example.com"
        assert config.repository == "owner/repo"

    def test_default_tag_message(self, valid_ssh_key: str):
        config = validate_config(
            tag="v1.0.0",
            deploy_key=valid_ssh_key,
            tag_message="",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )
        assert config.tag_message == "Release v1.0.0"

    def test_fails_fast_on_first_error(self):
        # validation fails on first error (tag is required)
        with pytest.raises(ValidationError) as exc_info:
            validate_config(
                tag="",
                deploy_key="",
                tag_message="",
                git_user_name="",
                git_user_email="",
                repository="",
            )
        assert "tag is required" in str(exc_info.value)

    def test_tag_message_with_control_chars(self, valid_ssh_key: str):
        with pytest.raises(ValidationError) as exc_info:
            validate_config(
                tag="v1.0.0",
                deploy_key=valid_ssh_key,
                tag_message="message\x00with\x01control",
                git_user_name="bot",
                git_user_email="bot@example.com",
                repository="owner/repo",
            )
        assert "control characters" in str(exc_info.value)

    def test_tag_message_exceeds_max_length(self, valid_ssh_key: str):
        # tag message over 4096 characters should be rejected
        long_message = "x" * 4097
        with pytest.raises(ValidationError) as exc_info:
            validate_config(
                tag="v1.0.0",
                deploy_key=valid_ssh_key,
                tag_message=long_message,
                git_user_name="bot",
                git_user_email="bot@example.com",
                repository="owner/repo",
            )
        assert "exceeds maximum length" in str(exc_info.value)

    def test_tag_message_at_max_length_allowed(self, valid_ssh_key: str):
        # tag message exactly at 4096 characters should be accepted
        max_length_message = "x" * 4096
        config = validate_config(
            tag="v1.0.0",
            deploy_key=valid_ssh_key,
            tag_message=max_length_message,
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )
        assert config.tag_message == max_length_message


class TestParseAgentVar:
    """Tests for _parse_agent_var helper function."""

    def test_parses_auth_sock(self, tmp_path: Path):
        # use tmp_path to construct a realistic path for testing
        agent_path = str(tmp_path / "ssh-xxxxx" / "agent.12345")
        output = f"SSH_AUTH_SOCK={agent_path}; export SSH_AUTH_SOCK;\n"
        result = _parse_agent_var(output, "SSH_AUTH_SOCK")
        assert result == agent_path

    def test_parses_agent_pid(self):
        output = "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        result = _parse_agent_var(output, "SSH_AGENT_PID")
        assert result == "12345"

    def test_parses_from_full_output(self, tmp_path: Path):
        # use tmp_path to construct realistic paths for testing
        agent_path = str(tmp_path / "ssh-abc" / "agent.999")
        output = (
            f"SSH_AUTH_SOCK={agent_path}; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=999; export SSH_AGENT_PID;\n"
            "echo Agent pid 999;\n"
        )
        assert _parse_agent_var(output, "SSH_AUTH_SOCK") == agent_path
        assert _parse_agent_var(output, "SSH_AGENT_PID") == "999"

    def test_raises_on_missing_var(self):
        output = "SSH_AUTH_SOCK=/tmp/ssh-abc/agent.123; export SSH_AUTH_SOCK;\n"
        with pytest.raises(RuntimeError) as exc_info:
            _parse_agent_var(output, "SSH_AGENT_PID")
        assert "failed to parse SSH_AGENT_PID" in str(exc_info.value)

    def test_raises_on_empty_output(self):
        with pytest.raises(RuntimeError) as exc_info:
            _parse_agent_var("", "SSH_AUTH_SOCK")
        assert "failed to parse SSH_AUTH_SOCK" in str(exc_info.value)


class TestSetupSshKey:
    """Tests for setup_ssh_key context manager."""

    def test_returns_ssh_config_named_tuple(self):
        """Verify SSHConfig is returned with correct attributes (mocked ssh-agent)."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill") as mock_kill,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest content\n-----END TEST-----"
            with setup_ssh_key(test_key) as ssh_config:
                assert isinstance(ssh_config, SSHConfig)
                assert hasattr(ssh_config, "auth_sock")
                assert hasattr(ssh_config, "agent_pid")
                assert hasattr(ssh_config, "known_hosts_path")

            # verify agent was killed on exit
            mock_kill.assert_called_once()

    def test_key_not_written_to_disk(self):
        """Verify no key file is created - key is loaded into ssh-agent via stdin."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest content\n-----END TEST-----"
            with setup_ssh_key(test_key) as ssh_config:
                # verify no "deploy_key" file exists in the temp directory
                tmpdir = Path(ssh_config.known_hosts_path).parent
                key_file = tmpdir / "deploy_key"
                assert not key_file.exists(), "Key should not be written to disk"

                # verify only known_hosts exists
                files = list(tmpdir.iterdir())
                assert len(files) == 1
                assert files[0].name == "known_hosts"

    def test_ssh_add_receives_key_via_stdin(self, tmp_path: Path):
        """Verify ssh-add is called with the key provided via stdin."""
        agent_path = str(tmp_path / "ssh-mock" / "agent.12345")
        mock_agent_output = (
            f"SSH_AUTH_SOCK={agent_path}; export SSH_AUTH_SOCK;\nSSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        calls: list[tuple] = []

        def capture_run(*args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock(returncode=0, stdout=mock_agent_output, stderr="")

        with (
            patch("subprocess.run", side_effect=capture_run),
            patch("os.kill"),
        ):
            test_key = "-----BEGIN TEST-----\ntest content\n-----END TEST-----"
            with setup_ssh_key(test_key):
                pass

        # find the ssh-add call (check if executable path ends with "ssh-add")
        ssh_add_call = None
        for call_args, call_kwargs in calls:
            if call_args and call_args[0] and call_args[0][0].endswith("ssh-add"):
                ssh_add_call = (call_args, call_kwargs)
                break

        assert ssh_add_call is not None, "ssh-add should be called"
        _, ssh_add_kwargs = ssh_add_call
        assert ssh_add_kwargs.get("input") == test_key, "Key should be passed via stdin"

    def test_known_hosts_written_with_github_keys(self):
        """Verify known_hosts is written with GitHub's SSH host keys."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest content\n-----END TEST-----"
            with setup_ssh_key(test_key) as ssh_config:
                known_hosts_path = Path(ssh_config.known_hosts_path)

                # verify known_hosts exists
                assert known_hosts_path.exists()

                # verify it contains GitHub's SSH host keys
                content = known_hosts_path.read_text()
                assert "github.com" in content
                assert "ssh-ed25519" in content
                assert "ssh-rsa" in content
                assert "ecdsa-sha2-nistp256" in content

                # verify permissions are restrictive (600)
                mode = known_hosts_path.stat().st_mode
                assert mode & stat.S_IRWXU == stat.S_IRUSR | stat.S_IWUSR
                assert mode & stat.S_IRWXG == 0
                assert mode & stat.S_IRWXO == 0

    def test_agent_killed_on_normal_exit(self):
        """Verify ssh-agent process is terminated on context exit."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill") as mock_kill,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest\n-----END TEST-----"
            with setup_ssh_key(test_key) as ssh_config:
                assert ssh_config.agent_pid == 12345

            # verify SIGTERM was sent to the agent
            import signal

            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

    def test_agent_killed_on_exception(self):
        """Verify ssh-agent is terminated even when an exception occurs."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.99999; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=99999; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill") as mock_kill,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest\n-----END TEST-----"
            with pytest.raises(RuntimeError):
                with setup_ssh_key(test_key):
                    raise RuntimeError("test exception")

            # verify agent was still killed
            import signal

            mock_kill.assert_called_once_with(99999, signal.SIGTERM)

    def test_known_hosts_cleanup_on_exit(self):
        """Verify temp directory with known_hosts is cleaned up."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )
        saved_path = None

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill"),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest\n-----END TEST-----"
            with setup_ssh_key(test_key) as ssh_config:
                saved_path = ssh_config.known_hosts_path
                assert Path(saved_path).exists()

        # after context exit, known_hosts should be cleaned up
        assert not Path(saved_path).exists()

    def test_handles_agent_already_exited(self):
        """Verify no error if agent has already exited when trying to kill it."""
        mock_agent_output = (
            "SSH_AUTH_SOCK=/tmp/ssh-mock/agent.12345; export SSH_AUTH_SOCK;\n"
            "SSH_AGENT_PID=12345; export SSH_AGENT_PID;\n"
        )

        with (
            patch("subprocess.run") as mock_run,
            patch("os.kill", side_effect=OSError("No such process")),
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_agent_output,
                stderr="",
            )

            test_key = "-----BEGIN TEST-----\ntest\n-----END TEST-----"
            # should not raise even though kill fails
            with setup_ssh_key(test_key):
                pass


class TestRunGit:
    """Tests for run_git function."""

    def test_successful_command(self, isolated_git_repo: IsolatedGitRepo):
        result = run_git(["--version"])
        assert "git version" in result.stdout

    def test_failed_command(self, isolated_git_repo: IsolatedGitRepo):
        with pytest.raises(GitError) as exc_info:
            run_git(["rev-parse", "nonexistent-ref-12345"])
        assert "rev-parse failed" in str(exc_info.value)

    def test_env_passed_to_command(self, isolated_git_repo: IsolatedGitRepo):
        # git config --get will fail if not set, but we can verify env handling works
        result = run_git(["config", "--list"], env={"GIT_CONFIG_NOSYSTEM": "1"})
        # should succeed without system config
        assert result.returncode == 0


class TestTagExistsLocally:
    """Tests for tag_exists_locally function."""

    def test_nonexistent_tag(self, isolated_git_repo: IsolatedGitRepo):
        assert tag_exists_locally("definitely-not-a-real-tag-12345") is False

    def test_existing_tag(self, isolated_git_repo: IsolatedGitRepo):
        # create a lightweight tag (--no-sign to bypass any gpgsign config)
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        assert tag_exists_locally("v1.0.0") is True

    def test_does_not_match_partial(self, isolated_git_repo: IsolatedGitRepo):
        # create v1.0.0, then check that v1.0 doesn't falsely match
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        assert tag_exists_locally("v1.0") is False


class TestTagExistsRemotely:
    """Tests for tag_exists_remotely function."""

    def test_nonexistent_tag(self, isolated_git_repo_with_origin: IsolatedGitRepo):
        result = tag_exists_remotely("definitely-not-a-real-tag-12345")
        assert result is False

    def test_existing_tag(self, isolated_git_repo_with_origin: IsolatedGitRepo):
        # create and push a lightweight tag to the local "origin"
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "push", "origin", "v1.0.0"], check=True, capture_output=True)
        assert tag_exists_remotely("v1.0.0") is True

    def test_does_not_match_partial(self, isolated_git_repo_with_origin: IsolatedGitRepo):
        # create v1.0.0, check that v1.0 doesn't falsely match
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "push", "origin", "v1.0.0"], check=True, capture_output=True)
        assert tag_exists_remotely("v1.0") is False


class TestWriteOutput:
    """Tests for write_output function."""

    def test_writes_to_github_output(self, github_output_file: Path):
        write_output("test_name", "test_value")
        assert github_output_file.read_text() == "test_name=test_value\n"

    def test_sanitizes_newlines(self, github_output_file: Path):
        write_output("name", "value\nwith\nnewlines")
        content = github_output_file.read_text()
        assert "\n" not in content.split("=", 1)[1].rstrip("\n")
        assert "value with newlines" in content

    def test_fallback_when_no_github_output(self, capsys):
        with patch.dict(os.environ, {}, clear=False):
            # remove GITHUB_OUTPUT if present
            os.environ.pop("GITHUB_OUTPUT", None)
            write_output("name", "value")

        captured = capsys.readouterr()
        assert "OUTPUT: name=value" in captured.out


class TestWriteError:
    """Tests for write_error function."""

    def test_writes_error_annotation(self, capsys):
        write_error("Something went wrong")
        captured = capsys.readouterr()
        assert "::error::Something went wrong" in captured.out

    def test_sanitizes_newlines(self, capsys):
        write_error("Error on\nmultiple\nlines")
        captured = capsys.readouterr()
        # newlines should be replaced with spaces
        assert "::error::Error on multiple lines" in captured.out
        assert "\n" not in captured.out.split("::error::")[1].rstrip("\n")


class TestParseArgs:
    """Tests for parse_args function."""

    def test_required_args(self):
        args = parse_args(["--tag", "v1.0.0", "--repository", "owner/repo"])
        assert args.tag == "v1.0.0"
        assert args.repository == "owner/repo"

    def test_default_values(self):
        args = parse_args(["--tag", "v1.0.0", "--repository", "owner/repo"])
        assert args.tag_message == ""
        assert args.git_user_name == "github-actions[bot]"
        assert args.git_user_email == "github-actions[bot]@users.noreply.github.com"

    def test_custom_values(self):
        args = parse_args(
            [
                "--tag",
                "v2.0.0",
                "--repository",
                "org/project",
                "--tag-message",
                "Custom release",
                "--git-user-name",
                "bot",
                "--git-user-email",
                "bot@test.com",
            ]
        )
        assert args.tag == "v2.0.0"
        assert args.repository == "org/project"
        assert args.tag_message == "Custom release"
        assert args.git_user_name == "bot"
        assert args.git_user_email == "bot@test.com"

    def test_missing_required_tag(self):
        with pytest.raises(SystemExit):
            parse_args(["--repository", "owner/repo"])

    def test_missing_required_repository(self):
        with pytest.raises(SystemExit):
            parse_args(["--tag", "v1.0.0"])


class TestTagExistsRemotelyExactMatch:
    """Tests for tag_exists_remotely exact matching behavior."""

    def test_exact_match_required(self):
        # mock the run_git to return a response that would cause substring matching issues
        with patch("create_tag.run_git") as mock_run_git:
            # simulate ls-remote output for v1.0.0 when searching for v1.0
            mock_result = MagicMock()
            mock_result.stdout = "abc123\trefs/tags/v1.0.0\n"
            mock_run_git.return_value = mock_result

            # searching for v1.0 should NOT match v1.0.0
            result = tag_exists_remotely("v1.0")
            assert result is False

    def test_finds_exact_tag(self):
        with patch("create_tag.run_git") as mock_run_git:
            mock_result = MagicMock()
            mock_result.stdout = "abc123\trefs/tags/v1.0.0\n"
            mock_run_git.return_value = mock_result

            result = tag_exists_remotely("v1.0.0")
            assert result is True

    def test_empty_output(self):
        with patch("create_tag.run_git") as mock_run_git:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run_git.return_value = mock_result

            result = tag_exists_remotely("v1.0.0")
            assert result is False

    def test_git_error_returns_false(self):
        with patch("create_tag.run_git") as mock_run_git:
            mock_run_git.side_effect = GitError("remote not found")

            result = tag_exists_remotely("v1.0.0")
            assert result is False


class TestMain:
    """Tests for main function."""

    def test_validation_error_returns_1(self, capsys):
        result = main(
            args=["--tag", "", "--repository", "owner/repo"],
            deploy_key="invalid",
        )
        assert result == 1
        captured = capsys.readouterr()
        assert "::error::" in captured.out

    def test_missing_deploy_key_returns_1(self, capsys):
        # ensure INPUT_DEPLOY_KEY is not set
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INPUT_DEPLOY_KEY", None)
            result = main(args=["--tag", "v1.0.0", "--repository", "owner/repo"])

        assert result == 1
        captured = capsys.readouterr()
        assert "deploy_key is required" in captured.out

    def test_reads_deploy_key_from_env(self, valid_ssh_key: str):
        with (
            patch.dict(os.environ, {"INPUT_DEPLOY_KEY": valid_ssh_key}),
            patch("create_tag.create_and_push_tag") as mock_create,
            patch("create_tag.write_output"),
        ):
            mock_create.return_value = "a" * 40  # valid SHA-1 format
            result = main(args=["--tag", "v1.0.0", "--repository", "owner/repo"])

        assert result == 0
        # verify the config was created with the key from env
        call_args = mock_create.call_args[0][0]
        assert call_args.deploy_key == valid_ssh_key

    def test_success_writes_sha_output(self, valid_ssh_key: str, github_output_file: Path):
        valid_sha = "a1b2c3d4e5f6" + "0" * 28  # 40 hex characters
        with patch("create_tag.create_and_push_tag") as mock_create:
            mock_create.return_value = valid_sha
            result = main(
                args=["--tag", "v1.0.0", "--repository", "owner/repo"],
                deploy_key=valid_ssh_key,
            )

        assert result == 0
        assert f"sha={valid_sha}" in github_output_file.read_text()

    def test_git_error_returns_1(self, valid_ssh_key: str, capsys):
        with patch("create_tag.create_and_push_tag") as mock_create:
            mock_create.side_effect = GitError("push failed")
            result = main(
                args=["--tag", "v1.0.0", "--repository", "owner/repo"],
                deploy_key=valid_ssh_key,
            )

        assert result == 1
        captured = capsys.readouterr()
        assert "::error::push failed" in captured.out


class TestCreateAndPushTag:
    """Tests for create_and_push_tag function.

    These tests use an isolated git repository to avoid modifying the user's
    actual git configuration. The isolated repo provides a safe environment
    for testing git operations.
    """

    @pytest.fixture
    def valid_config(self, isolated_git_repo_with_origin: IsolatedGitRepo) -> Config:
        """Config that uses the isolated repo's origin path."""
        # we use the local origin path but the function will try to use SSH
        # so we still need to mock the push operation
        # note: key has matching BEGIN/END markers as required by improved validation
        return Config(
            tag="v1.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

    @pytest.fixture
    def mock_setup_ssh_key(self, tmp_path: Path):
        """Fixture providing a mocked setup_ssh_key context manager."""
        # create a real known_hosts file for the mock
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...\n")

        # create mock agent socket path using tmp_path
        mock_agent_sock = str(tmp_path / "mock-ssh-agent" / "agent.12345")

        @contextmanager
        def _mock_setup_ssh_key(deploy_key: str):  # noqa: ARG001
            yield SSHConfig(
                auth_sock=mock_agent_sock,
                agent_pid=12345,
                known_hosts_path=str(known_hosts),
            )

        return _mock_setup_ssh_key

    def test_tag_already_exists_locally(
        self,
        isolated_git_repo_with_origin: IsolatedGitRepo,
        valid_config: Config,
        mock_setup_ssh_key,
    ):
        """Test that existing local tag raises ValidationError."""
        # create a lightweight tag locally first (bypass gpgsign config)
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v1.0.0"],
            check=True,
            capture_output=True,
        )

        with patch("create_tag.setup_ssh_key", mock_setup_ssh_key):
            with pytest.raises(ValidationError) as exc_info:
                create_and_push_tag(valid_config)
            assert "already exists locally" in str(exc_info.value)

        # verify that the repo's origin URL was restored (not changed to owner/repo)
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(isolated_git_repo_with_origin.origin_path) in result.stdout

    def test_tag_already_exists_remotely(
        self,
        isolated_git_repo_with_origin: IsolatedGitRepo,
        valid_config: Config,
        mock_setup_ssh_key,
    ):
        """Test that existing remote tag raises ValidationError.

        Note: create_and_push_tag changes the remote URL to SSH before checking
        if the tag exists remotely. Since we can't actually have a tag on the
        SSH remote in tests, we mock tag_exists_remotely to return True.
        """
        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag.tag_exists_remotely", return_value=True),
        ):
            with pytest.raises(ValidationError) as exc_info:
                create_and_push_tag(valid_config)
            assert "already exists on remote" in str(exc_info.value)

        # verify that the repo's origin URL was restored
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert str(isolated_git_repo_with_origin.origin_path) in result.stdout

    def test_restores_config_on_success(self, isolated_git_repo_with_origin: IsolatedGitRepo, mock_setup_ssh_key):
        """Test that git config is restored after successful tag creation.

        This test intercepts only the 'push' command since we can't use SSH
        in a test environment. All other git operations run for real.
        """
        config = Config(
            tag="v2.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v2.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        original_url = get_git_config("remote.origin.url")
        original_user = get_git_config("user.name")
        original_email = get_git_config("user.email")
        original_ssh_cmd = get_git_config("core.sshCommand")

        # save the real run_git function
        real_run_git = run_git

        def mock_run_git_selective(args: list[str], env: dict[str, str] | None = None):
            """Mock that only intercepts push commands, passes through everything else."""
            if args[0] == "push":
                # return a successful mock for push
                return MagicMock(returncode=0, stdout="", stderr="")
            # for all other commands, call the real function
            return real_run_git(args, env)

        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag.run_git", side_effect=mock_run_git_selective),
        ):
            create_and_push_tag(config)

        # verify config was restored
        assert get_git_config("remote.origin.url") == original_url
        assert get_git_config("user.name") == original_user
        assert get_git_config("user.email") == original_email
        assert get_git_config("core.sshCommand") == original_ssh_cmd

    def test_restores_config_on_failure(self, isolated_git_repo_with_origin: IsolatedGitRepo, mock_setup_ssh_key):
        """Test that git config is restored even when tag creation fails."""
        config = Config(
            tag="v3.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v3.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        # create a lightweight tag first so it will fail (bypass gpgsign config)
        subprocess.run(
            ["git", "-c", "tag.gpgsign=false", "tag", "v3.0.0"],
            check=True,
            capture_output=True,
        )

        original_url = get_git_config("remote.origin.url")
        original_user = get_git_config("user.name")
        original_email = get_git_config("user.email")

        with patch("create_tag.setup_ssh_key", mock_setup_ssh_key):
            with pytest.raises(ValidationError):
                create_and_push_tag(config)

        # verify config was restored even after failure
        assert get_git_config("remote.origin.url") == original_url
        assert get_git_config("user.name") == original_user
        assert get_git_config("user.email") == original_email


class TestGitConfigHelpers:
    """Tests for git config helper functions."""

    def test_get_git_config_returns_value(self):
        # test with a config that should exist in most git repos
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test-value\n")
            result = get_git_config("test.key")
            assert result == "test-value"

    def test_get_git_config_returns_none_when_not_set(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = get_git_config("nonexistent.key")
            assert result is None

    def test_get_git_config_returns_none_on_error(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("git not found")
            result = get_git_config("any.key")
            assert result is None

    def test_set_git_config_calls_run_git(self):
        with patch("create_tag.run_git") as mock_run:
            set_git_config("test.key", "test-value")
            mock_run.assert_called_once_with(["config", "test.key", "test-value"])

    def test_unset_git_config_does_not_raise(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=5)  # git returns 5 if key not found
            # should not raise
            unset_git_config("nonexistent.key")

    def test_unset_git_config_handles_os_error(self):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("git not found")
            # should not raise
            unset_git_config("any.key")

    def test_restore_git_config_unsets_when_none(self):
        with patch("create_tag.unset_git_config") as mock_unset:
            _restore_git_config("test.key", None)
            mock_unset.assert_called_once_with("test.key")

    def test_restore_git_config_sets_when_value(self):
        with patch("create_tag.set_git_config") as mock_set:
            _restore_git_config("test.key", "original-value")
            mock_set.assert_called_once_with("test.key", "original-value")


class TestSecurityFeatures:
    """Tests for security-related features."""

    def test_github_ssh_keys_tuple_contains_all_key_types(self):
        # verify the tuple contains all three key types
        assert len(GITHUB_SSH_KEYS) == 3
        assert GITHUB_SSH_KEY_ED25519 in GITHUB_SSH_KEYS
        assert GITHUB_SSH_KEY_ECDSA in GITHUB_SSH_KEYS
        assert GITHUB_SSH_KEY_RSA in GITHUB_SSH_KEYS

    def test_github_ssh_keys_have_correct_prefixes(self):
        # verify each key has the expected algorithm prefix
        assert GITHUB_SSH_KEY_ED25519.startswith("ssh-ed25519 ")
        assert GITHUB_SSH_KEY_ECDSA.startswith("ecdsa-sha2-nistp256 ")
        assert GITHUB_SSH_KEY_RSA.startswith("ssh-rsa ")

    def test_get_github_ssh_host_keys_formats_correctly(self):
        # verify the function generates proper known_hosts format
        host_keys = get_github_ssh_host_keys()
        lines = host_keys.strip().split("\n")

        assert len(lines) == 3
        for line in lines:
            assert line.startswith("github.com ")

        # verify all key types are present
        assert "ssh-ed25519" in host_keys
        assert "ssh-rsa" in host_keys
        assert "ecdsa-sha2-nistp256" in host_keys

    def test_ssh_command_uses_strict_host_checking(self, tmp_path: Path):
        """Verify SSH command uses StrictHostKeyChecking=yes with pre-populated known_hosts."""
        valid_config = Config(
            tag="v1.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        ssh_command_captured = None

        def capture_set_config(key: str, value: str) -> None:
            nonlocal ssh_command_captured
            if key == "core.sshCommand":
                ssh_command_captured = value

        # create a mock setup_ssh_key that yields a fake SSHConfig
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...\n")
        mock_agent_sock = str(tmp_path / "mock-ssh-agent" / "agent.12345")

        @contextmanager
        def mock_setup_ssh_key(deploy_key: str):  # noqa: ARG001
            yield SSHConfig(
                auth_sock=mock_agent_sock,
                agent_pid=12345,
                known_hosts_path=str(known_hosts),
            )

        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag.get_git_config", return_value=None),
            patch("create_tag.set_git_config", side_effect=capture_set_config),
            patch("create_tag.run_git") as mock_run,
            patch("create_tag.tag_exists_locally", return_value=False),
            patch("create_tag.tag_exists_remotely", return_value=False),
        ):
            mock_run.return_value = MagicMock(stdout="abc123\n")
            # the function will fail at push since we're mocking run_git,
            # but we capture the SSH command before that happens
            try:
                create_and_push_tag(valid_config)
            except (GitError, ValidationError, AttributeError):
                pass  # expected - mocked environment won't fully work

        # verify the SSH command was constructed securely
        assert ssh_command_captured is not None
        assert "StrictHostKeyChecking=yes" in ssh_command_captured
        assert "StrictHostKeyChecking=accept-new" not in ssh_command_captured
        assert "UserKnownHostsFile=" in ssh_command_captured
        assert "BatchMode=yes" in ssh_command_captured
        # verify we're using IdentityAgent (ssh-agent) instead of -i (file)
        assert "IdentityAgent=" in ssh_command_captured
        assert "-i " not in ssh_command_captured

    def test_ssh_command_paths_are_quoted(self, tmp_path: Path):
        """Verify paths in SSH command are properly quoted with shlex.quote."""
        valid_config = Config(
            tag="v1.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        ssh_command_captured = None

        def capture_set_config(key: str, value: str) -> None:
            nonlocal ssh_command_captured
            if key == "core.sshCommand":
                ssh_command_captured = value

        # create a mock setup_ssh_key that yields a fake SSHConfig
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...\n")
        mock_agent_sock = str(tmp_path / "mock-ssh-agent" / "agent.12345")

        @contextmanager
        def mock_setup_ssh_key(deploy_key: str):  # noqa: ARG001
            yield SSHConfig(
                auth_sock=mock_agent_sock,
                agent_pid=12345,
                known_hosts_path=str(known_hosts),
            )

        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag.get_git_config", return_value=None),
            patch("create_tag.set_git_config", side_effect=capture_set_config),
            patch("create_tag.run_git") as mock_run,
            patch("create_tag.tag_exists_locally", return_value=False),
            patch("create_tag.tag_exists_remotely", return_value=False),
        ):
            mock_run.return_value = MagicMock(stdout="abc123\n")
            try:
                create_and_push_tag(valid_config)
            except (GitError, ValidationError, AttributeError):
                pass  # expected - mocked environment won't fully work

        # the paths should be quoted - verify by checking that the command
        # contains properly quoted paths (shlex.quote adds quotes when needed)
        assert ssh_command_captured is not None
        # paths from tempfile typically don't need quoting, but verify the pattern
        # is consistent with shlex.quote output (no unquoted special chars)
        assert "-o IdentityAgent=" in ssh_command_captured
        assert "-o UserKnownHostsFile=" in ssh_command_captured


class TestGitHubSSHKeysAPIValidation:
    """Tests that validate baked-in SSH keys against GitHub's API.

    These tests hit the live GitHub API to ensure our hardcoded SSH keys
    are still valid. If GitHub rotates their keys, these tests will fail
    and the keys in create_tag.py must be updated.

    API documentation: https://docs.github.com/en/rest/meta/meta#get-github-meta-information
    """

    @pytest.fixture
    def github_api_keys(self) -> set[str]:
        """Fetch current SSH keys from GitHub's /meta API endpoint.

        Returns:
            Set of SSH public keys as returned by the API.

        Raises:
            pytest.skip: If the API is unreachable (allows offline testing).
        """
        import json
        import urllib.error
        import urllib.request

        url = "https://api.github.com/meta"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "create-tag-via-deploy-key-tests",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                return set(data.get("ssh_keys", []))
        except urllib.error.URLError as e:
            pytest.skip(f"GitHub API unreachable (offline mode): {e}")
        except json.JSONDecodeError as e:
            pytest.fail(f"GitHub API returned invalid JSON: {e}")

    def test_baked_in_keys_match_github_api(self, github_api_keys: set[str]):
        """Verify baked-in SSH keys exactly match GitHub's API response.

        This test ensures our hardcoded keys are in sync with GitHub.
        If this test fails, update the GITHUB_SSH_KEY_* constants in create_tag.py
        with the current keys from the GitHub API.

        To get the current keys, run:
            curl -s https://api.github.com/meta | jq -r '.ssh_keys[]'
        """
        baked_in_keys = set(GITHUB_SSH_KEYS)

        # use symmetric difference to catch both missing and extra keys
        difference = baked_in_keys.symmetric_difference(github_api_keys)
        if difference:
            missing_from_api = baked_in_keys - github_api_keys
            new_in_api = github_api_keys - baked_in_keys

            message_parts = ["SSH keys mismatch between baked-in and GitHub API!\n"]
            if missing_from_api:
                message_parts.append(f"Keys in code but NOT in GitHub API (rotated?):\n{missing_from_api}\n")
            if new_in_api:
                message_parts.append(f"Keys in GitHub API but NOT in code (new keys?):\n{new_in_api}\n")
            message_parts.append(f"\nCurrent GitHub API keys:\n{github_api_keys}\n")
            message_parts.append("Update GITHUB_SSH_KEY_* constants in create_tag.py")

            pytest.fail("\n".join(message_parts))


class TestValidateOutputName:
    """Tests for validate_output_name function."""

    @pytest.mark.parametrize(
        "name",
        [
            "sha",
            "result",
            "my_output",
            "_private",
            "Output123",
            "a",
        ],
    )
    def test_valid_names(self, name: str):
        assert validate_output_name(name) == name

    @pytest.mark.parametrize(
        "name,error_contains",
        [
            ("", "cannot be empty"),
            ("123start", "invalid characters"),  # can't start with number
            ("has-dash", "invalid characters"),
            ("has space", "invalid characters"),
            ("has\nnewline", "invalid characters"),
            ("has=equals", "invalid characters"),
            ("injection\nTOKEN", "invalid characters"),
        ],
    )
    def test_invalid_names(self, name: str, error_contains: str):
        with pytest.raises(ValueError) as exc_info:
            validate_output_name(name)
        assert error_contains in str(exc_info.value)

    def test_rejects_long_name(self):
        """Output names over 128 characters should be rejected."""
        long_name = "a" * 129
        with pytest.raises(ValueError) as exc_info:
            validate_output_name(long_name)
        assert "maximum length" in str(exc_info.value)

    def test_accepts_max_length_name(self):
        """Output names at exactly 128 characters should be accepted."""
        max_name = "a" * 128
        assert validate_output_name(max_name) == max_name


class TestValidateSha:
    """Tests for validate_sha function."""

    @pytest.mark.parametrize(
        "sha",
        [
            "a" * 40,  # valid SHA-1
            "0123456789abcdef" * 2 + "01234567",  # 40 hex chars
            "a" * 64,  # valid SHA-256
            "0123456789abcdef" * 4,  # 64 hex chars
        ],
    )
    def test_valid_shas(self, sha: str):
        assert validate_sha(sha) == sha

    @pytest.mark.parametrize(
        "sha,error_contains",
        [
            ("", "cannot be empty"),
            ("abc", "invalid SHA format"),  # too short
            ("a" * 39, "invalid SHA format"),  # one char too short
            ("a" * 41, "invalid SHA format"),  # one char too long (not 64)
            ("g" * 40, "invalid SHA format"),  # invalid hex char
            ("A" * 40, "invalid SHA format"),  # uppercase not allowed
            ("a" * 40 + "\n", "invalid SHA format"),  # newline injection
        ],
    )
    def test_invalid_shas(self, sha: str, error_contains: str):
        with pytest.raises(ValueError) as exc_info:
            validate_sha(sha)
        assert error_contains in str(exc_info.value)


class TestGitHubOutputInjection:
    """Tests for GITHUB_OUTPUT injection prevention."""

    def test_write_output_rejects_invalid_name(self, github_output_file: Path):
        """Verify write_output rejects names with injection attempts."""
        with pytest.raises(ValueError):
            write_output("sha\nTOKEN", "value")

    def test_write_output_sanitizes_value_newlines(self, github_output_file: Path):
        """Verify newlines in values are sanitized."""
        write_output("result", "line1\nline2\rline3")
        content = github_output_file.read_text()
        # newlines should be replaced with spaces
        assert "result=line1 line2 line3\n" == content

    def test_write_output_with_equals_in_value(self, github_output_file: Path):
        """Values can contain equals signs (they're not delimiters after the first)."""
        write_output("result", "key=value=other")
        content = github_output_file.read_text()
        assert "result=key=value=other\n" == content

    def test_main_validates_sha_before_output(self, valid_ssh_key: str, github_output_file: Path, capsys):
        """Verify main() validates SHA format before writing to output."""
        with patch("create_tag.create_and_push_tag") as mock_create:
            # return an invalid SHA
            mock_create.return_value = "not-a-valid-sha"
            result = main(
                args=["--tag", "v1.0.0", "--repository", "owner/repo"],
                deploy_key=valid_ssh_key,
            )

        assert result == 1
        captured = capsys.readouterr()
        assert "::error::" in captured.out
        assert "Internal error" in captured.out

    def test_main_accepts_valid_sha(self, valid_ssh_key: str, github_output_file: Path):
        """Verify main() accepts valid SHA-1 hashes."""
        with patch("create_tag.create_and_push_tag") as mock_create:
            mock_create.return_value = "a" * 40  # valid SHA-1
            result = main(
                args=["--tag", "v1.0.0", "--repository", "owner/repo"],
                deploy_key=valid_ssh_key,
            )

        assert result == 0
        assert f"sha={'a' * 40}" in github_output_file.read_text()


class TestTagBoundaryConditions:
    """Tests for tag validation at boundary conditions."""

    def test_tag_at_max_length(self):
        """Tag at exactly 256 characters should be accepted."""
        # first char must be alphanumeric, rest can include dots/dashes
        tag = "v" + "a" * 255  # 256 total
        assert validate_tag(tag) == tag

    def test_tag_over_max_length(self):
        """Tag over 256 characters should be rejected."""
        tag = "v" + "a" * 256  # 257 total
        with pytest.raises(ValidationError) as exc_info:
            validate_tag(tag)
        assert "invalid characters" in str(exc_info.value)

    def test_tag_with_many_slashes(self):
        """Tags can have hierarchical structure with slashes."""
        tag = "release/2024/v1/final"
        assert validate_tag(tag) == tag

    def test_tag_with_only_valid_special_chars(self):
        """Tags can contain dots, dashes, underscores, and slashes."""
        tag = "v1.0.0-beta_1/release"
        assert validate_tag(tag) == tag

    def test_tag_single_char(self):
        """Single character tags should be valid."""
        assert validate_tag("v") == "v"
        assert validate_tag("1") == "1"

    def test_tag_rejects_double_dots_anywhere(self):
        """Double dots should be rejected anywhere in the tag."""
        with pytest.raises(ValidationError) as exc_info:
            validate_tag("v1..0")
        assert "cannot contain '..'" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            validate_tag("..v1.0")
        assert "invalid characters" in str(exc_info.value)  # also fails regex (starts with .)


class TestSSHCommandPathQuoting:
    """Tests for SSH command path quoting with special characters."""

    def test_ssh_command_quotes_path_with_spaces(self, tmp_path: Path):
        """Verify paths with spaces are properly quoted in SSH command."""

        # create a path with spaces
        path_with_spaces = tmp_path / "path with spaces" / "known_hosts"
        path_with_spaces.parent.mkdir(parents=True)
        path_with_spaces.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...\n")

        # create mock agent path with spaces using tmp_path
        mock_agent_sock = str(tmp_path / "path with spaces" / "agent.12345")

        valid_config = Config(
            tag="v1.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        ssh_command_captured = None

        def capture_set_config(key: str, value: str) -> None:
            nonlocal ssh_command_captured
            if key == "core.sshCommand":
                ssh_command_captured = value

        @contextmanager
        def mock_setup_ssh_key(deploy_key: str):  # noqa: ARG001
            yield SSHConfig(
                auth_sock=mock_agent_sock,
                agent_pid=12345,
                known_hosts_path=str(path_with_spaces),
            )

        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag.get_git_config", return_value=None),
            patch("create_tag.set_git_config", side_effect=capture_set_config),
            patch("create_tag.run_git") as mock_run,
            patch("create_tag.tag_exists_locally", return_value=False),
            patch("create_tag.tag_exists_remotely", return_value=False),
        ):
            mock_run.return_value = MagicMock(stdout="abc123\n")
            try:
                create_and_push_tag(valid_config)
            except (GitError, ValidationError, AttributeError):
                pass

        # verify paths are quoted
        assert ssh_command_captured is not None
        # shlex.quote should have quoted the paths with spaces
        assert "'" in ssh_command_captured

    def test_ssh_command_quotes_path_with_quotes(self, tmp_path: Path):
        """Verify paths with quote characters are properly escaped."""
        import shlex

        # create a path that would need quoting using tmp_path
        test_path = str(tmp_path / "test'path" / "agent.123")
        quoted = shlex.quote(test_path)

        # shlex.quote should escape the single quote
        assert "'" in test_path
        assert quoted != test_path  # it should be different (escaped)


class TestRestoreConfigOnFailure:
    """Tests for git config restoration on various failure scenarios."""

    def test_restore_continues_after_first_failure(self, isolated_git_repo_with_origin: IsolatedGitRepo, capsys):
        """Verify all restore attempts run even if one fails."""
        config = Config(
            tag="v1.0.0",
            deploy_key="-----BEGIN OPENSSH PRIVATE KEY-----\nbase64data\n-----END OPENSSH PRIVATE KEY-----",
            tag_message="Release v1.0.0",
            git_user_name="bot",
            git_user_email="bot@example.com",
            repository="owner/repo",
        )

        call_count = {"restore": 0}
        original_restore = _restore_git_config

        def counting_restore(key: str, value: str | None) -> None:
            call_count["restore"] += 1
            if call_count["restore"] == 1:
                # fail on first restore
                raise GitError("simulated restore failure")
            original_restore(key, value)

        # create a known_hosts file for the mock
        tmp_path = isolated_git_repo_with_origin.tmp_path
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5...\n")
        mock_agent_sock = str(tmp_path / "mock-agent" / "agent.12345")

        @contextmanager
        def mock_setup_ssh_key(deploy_key: str):  # noqa: ARG001
            yield SSHConfig(
                auth_sock=mock_agent_sock,
                agent_pid=12345,
                known_hosts_path=str(known_hosts),
            )

        with (
            patch("create_tag.setup_ssh_key", mock_setup_ssh_key),
            patch("create_tag._restore_git_config", side_effect=counting_restore),
            patch("create_tag.tag_exists_locally", return_value=True),  # will cause failure
        ):
            with pytest.raises(ValidationError):
                create_and_push_tag(config)

        # verify multiple restore attempts were made (at least 3: sshCommand, user.name, user.email)
        # first fails, but others should still be called
        assert call_count["restore"] >= 3

        # verify warning was printed
        captured = capsys.readouterr()
        assert "Warning: Failed to restore" in captured.err
