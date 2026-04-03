#!/usr/bin/env python3
"""
Create and push a git tag using a deploy key for authentication.

This script provides a secure way to push tags from CI without using
overly-permissive GITHUB_TOKEN permissions. By using a deploy key with
repository rulesets, we can allow tag pushes while blocking code pushes.

Security features:
- All subprocess calls use list arguments (no shell injection possible)
- Deploy key is loaded into ssh-agent via stdin (never written to disk)
- Input validation rejects potentially dangerous characters
- Output sanitization prevents GITHUB_OUTPUT injection
- Cleanup runs even on failure via context managers (agent is killed)
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

# full paths to executables on GitHub runners (Linux)
# this satisfies S607 (partial executable path) security checks
GIT_PATH = "/usr/bin/git"
SSH_AGENT_PATH = "/usr/bin/ssh-agent"
SSH_ADD_PATH = "/usr/bin/ssh-add"

# GitHub's official SSH host keys.
# Source: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
#
# To verify or update these keys:
#
#   Method 1 - Using GitHub's REST API (recommended, machine-readable):
#     curl -s https://api.github.com/meta | jq -r '.ssh_keys[]' | while read key; do
#       echo "github.com $key"
#     done
#
#   Method 2 - Using ssh-keyscan:
#     ssh-keyscan github.com 2>/dev/null | sort
#
#   Method 3 - Verify fingerprints against documentation:
#     https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
#     You can compute fingerprints with: ssh-keygen -lf <(echo "KEY_LINE_HERE")
#
# API documentation: https://docs.github.com/en/rest/meta/meta#get-github-meta-information
#
# IMPORTANT: These keys are validated against the GitHub API in test_create_tag.py.
# If GitHub rotates their keys, the test will fail and these values must be updated.

# individual SSH public keys from GitHub (without the "github.com" prefix)
# these are the raw key values as returned by the GitHub /meta API endpoint
GITHUB_SSH_KEY_ED25519 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
GITHUB_SSH_KEY_ECDSA = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg="  # noqa: E501
GITHUB_SSH_KEY_RSA = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk="  # noqa: E501

# tuple of all GitHub SSH keys for iteration
GITHUB_SSH_KEYS = (
    GITHUB_SSH_KEY_ED25519,
    GITHUB_SSH_KEY_ECDSA,
    GITHUB_SSH_KEY_RSA,
)


def get_github_ssh_host_keys() -> str:
    """Generate the known_hosts file content for GitHub.

    Returns:
        A string suitable for writing to a known_hosts file, with each key
        prefixed by "github.com ".
    """
    return "\n".join(f"github.com {key}" for key in GITHUB_SSH_KEYS)


class Config(NamedTuple):
    """Validated configuration for the tag operation."""

    tag: str
    deploy_key: str
    tag_message: str
    git_user_name: str
    git_user_email: str
    repository: str


class ValidationError(Exception):
    """Raised when input validation fails."""


class GitError(Exception):
    """Raised when a git operation fails."""


# pattern for valid git tag names (conservative subset)
# allows: alphanumeric, dots, dashes, underscores, slashes
# disallows: spaces, shell metacharacters, control characters
TAG_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,255}$")

# pattern for valid git user name/email (conservative)
# rejects shell metacharacters and control characters
SAFE_STRING_PATTERN = re.compile(r"^[a-zA-Z0-9@._\-\[\] ]{1,256}$")


def validate_tag(tag: str | None) -> str:
    """Validate tag name.

    Args:
        tag: The tag name to validate.

    Returns:
        The validated tag name.

    Raises:
        ValidationError: If the tag is invalid.
    """
    if not tag:
        raise ValidationError("tag is required")

    if not TAG_PATTERN.match(tag):
        raise ValidationError(
            f"tag contains invalid characters or format: '{tag}'. "
            "Must start with alphanumeric and contain only alphanumeric, dots, dashes, underscores, or slashes"
        )

    # additional safety checks
    if tag.startswith("-"):
        raise ValidationError("tag cannot start with a dash")

    if ".." in tag:
        raise ValidationError("tag cannot contain '..'")

    if tag.endswith(".lock"):
        raise ValidationError("tag cannot end with '.lock'")

    return tag


def validate_safe_string(value: str | None, field_name: str) -> str:
    """Validate a string contains only safe characters.

    Args:
        value: The string to validate.
        field_name: Name of the field for error messages.

    Returns:
        The validated string.

    Raises:
        ValidationError: If the string is invalid.
    """
    if not value:
        raise ValidationError(f"{field_name} is required")

    if not SAFE_STRING_PATTERN.match(value):
        raise ValidationError(
            f"{field_name} contains invalid characters: '{value}'. "
            "Must contain only alphanumeric, @, dots, dashes, underscores, brackets, or spaces"
        )

    return value


def validate_repository(repository: str | None) -> str:
    """Validate repository format.

    Args:
        repository: The repository in 'owner/repo' format.

    Returns:
        The validated repository string.

    Raises:
        ValidationError: If the repository format is invalid.
    """
    if not repository:
        raise ValidationError("repository is required")

    if "/" not in repository or repository.count("/") != 1:
        raise ValidationError(f"repository must be in 'owner/repo' format, got: {repository}")

    owner, repo = repository.split("/")
    if not owner or not repo:
        raise ValidationError(f"repository must be in 'owner/repo' format, got: {repository}")

    # validate characters (GitHub allows alphanumeric, dash, underscore, dot)
    repo_pattern = re.compile(r"^[a-zA-Z0-9._-]+$")
    if not repo_pattern.match(owner) or not repo_pattern.match(repo):
        raise ValidationError(f"repository contains invalid characters: {repository}")

    return repository


def validate_deploy_key(deploy_key: str | None) -> str:
    """Validate deploy key format.

    Args:
        deploy_key: The SSH private key.

    Returns:
        The validated deploy key.

    Raises:
        ValidationError: If the deploy key is invalid.
    """
    if not deploy_key:
        raise ValidationError("deploy_key is required")

    # size limit to prevent memory exhaustion (16KB is generous for SSH keys)
    max_key_size = 16 * 1024
    if len(deploy_key) > max_key_size:
        raise ValidationError(f"deploy_key exceeds maximum size of {max_key_size} bytes")

    # reject null bytes which could cause truncation issues
    if "\x00" in deploy_key:
        raise ValidationError("deploy_key contains invalid null bytes")

    # check for valid PEM format - must have matching BEGIN/END markers
    # this prevents attacks where someone embeds "PRIVATE KEY-----" in a comment
    stripped = deploy_key.strip()

    # valid private key headers we accept
    valid_headers = (
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",  # PKCS#8 format
    )

    header_found = None
    for header in valid_headers:
        if stripped.startswith(header):
            header_found = header
            break

    if not header_found:
        raise ValidationError(
            "deploy_key does not appear to be a valid SSH private key (must start with a valid private key header)"
        )

    # verify the corresponding END marker exists
    expected_footer = header_found.replace("BEGIN", "END")
    if not stripped.endswith(expected_footer):
        raise ValidationError(
            "deploy_key does not appear to be a valid SSH private key (missing or invalid end marker)"
        )

    return deploy_key


def validate_config(
    tag: str | None,
    deploy_key: str | None,
    tag_message: str | None,
    git_user_name: str | None,
    git_user_email: str | None,
    repository: str | None,
) -> Config:
    """Validate all inputs and return a Config object.

    Args:
        tag: The tag to create.
        deploy_key: SSH private key.
        tag_message: Message for the annotated tag.
        git_user_name: Git user.name for the tag.
        git_user_email: Git user.email for the tag.
        repository: Repository in 'owner/repo' format.

    Returns:
        A validated Config object.

    Raises:
        ValidationError: If any input is invalid.
    """
    # validate all inputs (fail-fast on first error)
    validated_tag = validate_tag(tag)
    validated_deploy_key = validate_deploy_key(deploy_key)
    validated_repository = validate_repository(repository)
    validated_git_user_name = validate_safe_string(git_user_name, "git_user_name")
    validated_git_user_email = validate_safe_string(git_user_email, "git_user_email")

    # tag_message is optional, but if provided, validate it
    final_tag_message = tag_message if tag_message else f"Release {validated_tag}"

    # validate tag message doesn't contain control characters (except newlines)
    if final_tag_message and re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", final_tag_message):
        raise ValidationError("tag_message contains invalid control characters")

    # limit tag message length to prevent resource exhaustion
    max_tag_message_length = 4096
    if len(final_tag_message) > max_tag_message_length:
        raise ValidationError(f"tag_message exceeds maximum length of {max_tag_message_length} characters")

    return Config(
        tag=validated_tag,
        deploy_key=validated_deploy_key,
        tag_message=final_tag_message,
        git_user_name=validated_git_user_name,
        git_user_email=validated_git_user_email,
        repository=validated_repository,
    )


def run_git(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command securely.

    Args:
        args: Git command arguments (without 'git' prefix).
        env: Optional environment variables to add.

    Returns:
        CompletedProcess with stdout/stderr captured.

    Raises:
        GitError: If the git command fails.
    """
    cmd = [GIT_PATH, *args]

    # merge with current environment
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        result = subprocess.run(  # noqa: S603 - cmd is built from validated inputs
            cmd,
            capture_output=True,
            text=True,
            check=False,  # we'll check manually for better error messages
            env=full_env,
        )
    except OSError as e:
        raise GitError(f"Failed to execute git: {e}") from e

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "no error output"
        raise GitError(f"git {args[0]} failed: {stderr}")

    return result


def get_git_config(key: str) -> str | None:
    """Get a git config value, returning None if not set.

    Args:
        key: The config key to get.

    Returns:
        The config value, or None if not set.
    """
    try:
        result = subprocess.run(  # noqa: S603 - key is used as git config key, not shell command
            [GIT_PATH, "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except OSError:
        return None


def set_git_config(key: str, value: str) -> None:
    """Set a git config value.

    Args:
        key: The config key to set.
        value: The value to set.
    """
    run_git(["config", key, value])


def unset_git_config(key: str) -> None:
    """Unset a git config value, ignoring errors if not set.

    Args:
        key: The config key to unset.
    """
    try:
        subprocess.run(  # noqa: S603 - key is used as git config key, not shell command
            [GIT_PATH, "config", "--unset", key],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        pass


class SSHConfig(NamedTuple):
    """SSH configuration returned by setup_ssh_key."""

    auth_sock: str  # SSH_AUTH_SOCK path for ssh-agent
    agent_pid: int  # ssh-agent PID for cleanup
    known_hosts_path: str


def _parse_agent_var(output: str, var_name: str) -> str:
    """Parse a variable from ssh-agent -s output.

    Args:
        output: The stdout from ssh-agent -s.
        var_name: The variable name to extract (e.g., SSH_AUTH_SOCK).

    Returns:
        The value of the variable.

    Raises:
        RuntimeError: If the variable cannot be parsed.
    """
    # output format: SSH_AUTH_SOCK=/tmp/ssh-xxx/agent.123; export SSH_AUTH_SOCK;
    match = re.search(rf"{var_name}=([^;]+);", output)
    if not match:
        raise RuntimeError(f"failed to parse {var_name} from ssh-agent output")
    return match.group(1)


@contextmanager
def setup_ssh_key(deploy_key: str) -> Iterator[SSHConfig]:
    """Set up SSH key in ssh-agent and known_hosts in a secure temp directory.

    Uses ssh-agent to hold the key in memory, avoiding disk writes for the
    private key material. The known_hosts file is still written to disk as
    it contains only GitHub's public host keys (not sensitive).

    Args:
        deploy_key: The SSH private key content.

    Yields:
        SSHConfig with the agent socket path, agent PID, and known_hosts path.
    """
    agent_pid: int | None = None

    # still need temp dir for known_hosts (required for MITM protection)
    with tempfile.TemporaryDirectory(prefix="deploy_key_") as tmpdir:
        tmpdir_path = Path(tmpdir)
        os.chmod(tmpdir_path, stat.S_IRWXU)  # 700

        # write known_hosts with GitHub's official SSH host keys
        known_hosts_path = tmpdir_path / "known_hosts"
        known_hosts_path.touch(mode=stat.S_IRUSR | stat.S_IWUSR)  # 600
        known_hosts_path.write_text(get_github_ssh_host_keys() + "\n")

        try:
            # start ssh-agent and parse output
            result = subprocess.run(  # noqa: S603 - using hardcoded SSH_AGENT_PATH constant
                [SSH_AGENT_PATH, "-s"],
                capture_output=True,
                text=True,
                check=True,
            )

            # parse SSH_AUTH_SOCK and SSH_AGENT_PID from output
            auth_sock = _parse_agent_var(result.stdout, "SSH_AUTH_SOCK")
            agent_pid = int(_parse_agent_var(result.stdout, "SSH_AGENT_PID"))

            # set environment for ssh-add
            env = os.environ.copy()
            env["SSH_AUTH_SOCK"] = auth_sock

            # load key into agent via stdin (no disk write)
            subprocess.run(  # noqa: S603 - using hardcoded SSH_ADD_PATH, input is validated
                [SSH_ADD_PATH, "-"],
                input=deploy_key,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            yield SSHConfig(
                auth_sock=auth_sock,
                agent_pid=agent_pid,
                known_hosts_path=str(known_hosts_path),
            )
        finally:
            # kill the ssh-agent process
            if agent_pid is not None:
                try:
                    os.kill(agent_pid, signal.SIGTERM)
                except OSError:
                    pass  # agent may have already exited


def tag_exists_locally(tag: str) -> bool:
    """Check if a tag exists locally.

    Args:
        tag: The tag name to check.

    Returns:
        True if the tag exists locally.
    """
    try:
        run_git(["rev-parse", tag])
        return True
    except GitError:
        return False


def tag_exists_remotely(tag: str) -> bool:
    """Check if a tag exists on the remote.

    Args:
        tag: The tag name to check.

    Returns:
        True if the tag exists on the remote.
    """
    try:
        result = run_git(["ls-remote", "--tags", "origin", tag])
        # check for exact ref match to avoid substring false positives
        # (e.g., "v1.0" matching "v1.0.0")
        # ls-remote output format: "<sha>\trefs/tags/<tag>"
        expected_ref = f"refs/tags/{tag}"
        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1] == expected_ref:
                return True
        return False
    except GitError:
        return False


def _restore_git_config(key: str, original_value: str | None) -> None:
    """Restore a git config value to its original state.

    Args:
        key: The config key to restore.
        original_value: The original value (None if was not set).
    """
    if original_value is None:
        unset_git_config(key)
    else:
        set_git_config(key, original_value)


def create_and_push_tag(config: Config) -> str:
    """Create and push a git tag using the deploy key.

    Args:
        config: Validated configuration.

    Returns:
        The commit SHA that was tagged.

    Raises:
        GitError: If any git operation fails.
        ValidationError: If the tag already exists.
    """
    # save original git config values for restoration
    original_ssh_command = get_git_config("core.sshCommand")
    original_remote_url = get_git_config("remote.origin.url")
    original_user_name = get_git_config("user.name")
    original_user_email = get_git_config("user.email")

    with setup_ssh_key(config.deploy_key) as ssh_config:
        try:
            # configure git to use ssh-agent with strict host verification
            # IdentityAgent specifies the agent socket (key is loaded in memory)
            # shlex.quote() prevents command injection via path manipulation
            # StrictHostKeyChecking=yes with pre-populated known_hosts prevents MITM attacks
            ssh_command = (
                f"ssh -o IdentityAgent={shlex.quote(ssh_config.auth_sock)} "
                f"-o UserKnownHostsFile={shlex.quote(ssh_config.known_hosts_path)} "
                f"-o StrictHostKeyChecking=yes "
                f"-o BatchMode=yes"
            )
            set_git_config("core.sshCommand", ssh_command)

            # set remote URL to SSH
            ssh_url = f"git@github.com:{config.repository}.git"
            run_git(["remote", "set-url", "origin", ssh_url])

            # configure git user
            set_git_config("user.name", config.git_user_name)
            set_git_config("user.email", config.git_user_email)

            # check if tag already exists
            if tag_exists_locally(config.tag):
                raise ValidationError(f"tag '{config.tag}' already exists locally")

            if tag_exists_remotely(config.tag):
                raise ValidationError(f"tag '{config.tag}' already exists on remote")

            # create annotated tag
            run_git(["tag", "-a", config.tag, "-m", config.tag_message])

            # push tag
            print(f"Pushing tag {config.tag} to origin...")
            run_git(["push", "origin", config.tag])

            # get the commit SHA
            result = run_git(["rev-parse", "HEAD"])
            sha = result.stdout.strip()

            print(f"Successfully created and pushed tag {config.tag} at {sha}")
            return sha
        finally:
            # restore original git config values
            # wrap each in try/except to ensure all cleanup attempts run
            restore_errors: list[str] = []

            try:
                _restore_git_config("core.sshCommand", original_ssh_command)
            except GitError as e:
                restore_errors.append(f"core.sshCommand: {e}")

            try:
                if original_remote_url is not None:
                    run_git(["remote", "set-url", "origin", original_remote_url])
            except GitError as e:
                restore_errors.append(f"remote.origin.url: {e}")

            try:
                _restore_git_config("user.name", original_user_name)
            except GitError as e:
                restore_errors.append(f"user.name: {e}")

            try:
                _restore_git_config("user.email", original_user_email)
            except GitError as e:
                restore_errors.append(f"user.email: {e}")

            # log any restore failures (but don't fail the operation)
            if restore_errors:
                print("Warning: Failed to restore some git config values:", file=sys.stderr)
                for error in restore_errors:
                    print(f"  - {error}", file=sys.stderr)


def validate_output_name(name: str) -> str:
    """Validate and sanitize an output name for GITHUB_OUTPUT.

    Args:
        name: The output name to validate.

    Returns:
        The validated output name.

    Raises:
        ValueError: If the name is invalid.
    """
    if not name:
        raise ValueError("output name cannot be empty")

    # output names must be alphanumeric with underscores only
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise ValueError(
            f"output name '{name}' contains invalid characters "
            "(must be alphanumeric with underscores, starting with letter or underscore)"
        )

    # reasonable length limit
    if len(name) > 128:
        raise ValueError("output name exceeds maximum length of 128 characters")

    return name


def validate_sha(sha: str) -> str:
    """Validate a git SHA format.

    Args:
        sha: The SHA to validate.

    Returns:
        The validated SHA.

    Raises:
        ValueError: If the SHA format is invalid.
    """
    if not sha:
        raise ValueError("SHA cannot be empty")

    # git SHA must be 40 hex characters (full SHA) or 64 for SHA-256
    # use \Z instead of $ to reject trailing newlines
    if not re.match(r"^[a-f0-9]{40}\Z", sha) and not re.match(r"^[a-f0-9]{64}\Z", sha):
        raise ValueError(f"invalid SHA format: {sha[:20]}..." if len(sha) > 20 else f"invalid SHA format: {sha}")

    return sha


def write_output(name: str, value: str) -> None:
    """Write an output to GITHUB_OUTPUT file.

    Args:
        name: Output name (must be alphanumeric with underscores).
        value: Output value.

    Raises:
        ValueError: If the name is invalid.
    """
    # validate and sanitize the name to prevent injection
    sanitized_name = validate_output_name(name)

    # sanitize value to prevent output injection
    # remove any characters that could break the output format
    sanitized_value = re.sub(r"[\r\n]", " ", value)

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:  # noqa: PTH123
            f.write(f"{sanitized_name}={sanitized_value}\n")
    else:
        # fallback for local testing
        print(f"OUTPUT: {sanitized_name}={sanitized_value}")


def write_error(message: str) -> None:
    """Write an error annotation to GitHub Actions.

    Args:
        message: Error message.
    """
    # sanitize message for GitHub Actions annotation format
    sanitized = re.sub(r"[\r\n]", " ", message)
    print(f"::error::{sanitized}")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        args: Command line arguments (defaults to sys.argv).

    Returns:
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Create and push a git tag using a deploy key",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--tag",
        required=True,
        help="The tag to create (e.g., v1.2.3)",
    )
    # NOTE: deploy-key is read from INPUT_DEPLOY_KEY environment variable
    # to avoid exposing the secret in process listings (ps, /proc/*/cmdline)
    parser.add_argument(
        "--tag-message",
        default="",
        help="Annotated tag message (defaults to 'Release <tag>')",
    )
    parser.add_argument(
        "--git-user-name",
        default="github-actions[bot]",
        help="Git user.name for the tag",
    )
    parser.add_argument(
        "--git-user-email",
        default="github-actions[bot]@users.noreply.github.com",
        help="Git user.email for the tag",
    )
    parser.add_argument(
        "--repository",
        required=True,
        help="Repository in 'owner/repo' format",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None, deploy_key: str | None = None) -> int:
    """Main entry point.

    Args:
        args: Command line arguments (defaults to sys.argv).
        deploy_key: Deploy key (defaults to INPUT_DEPLOY_KEY env var).

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parsed = parse_args(args)

    # read deploy key from environment variable to avoid exposing it in process listings
    if deploy_key is None:
        deploy_key = os.environ.get("INPUT_DEPLOY_KEY")

    try:
        config = validate_config(
            tag=parsed.tag,
            deploy_key=deploy_key,
            tag_message=parsed.tag_message,
            git_user_name=parsed.git_user_name,
            git_user_email=parsed.git_user_email,
            repository=parsed.repository,
        )
    except ValidationError as e:
        write_error(f"Validation failed: {e}")
        return 1

    try:
        sha = create_and_push_tag(config)
    except (ValidationError, GitError) as e:
        write_error(str(e))
        return 1

    # validate SHA format before writing to output (defense in depth)
    try:
        validated_sha = validate_sha(sha)
    except ValueError as e:
        write_error(f"Internal error: {e}")
        return 1

    write_output("sha", validated_sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
