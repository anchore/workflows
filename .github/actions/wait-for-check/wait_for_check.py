#!/usr/bin/env python3
"""
Wait for a GitHub check to complete and output its conclusion.

This script polls the GitHub API for a specific check run on a commit,
waiting until it completes or times out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Config(NamedTuple):
    """Validated configuration for the wait operation."""

    token: str
    repository: str
    check_name: str
    ref: str
    timeout_seconds: int
    interval_seconds: int


class ValidationError(Exception):
    """Raised when input validation fails."""


def validate_config(
    token: str | None,
    repository: str | None,
    check_name: str | None,
    ref: str | None,
    timeout_seconds: int,
    interval_seconds: int,
) -> Config:
    """Validate all inputs and return a Config object.

    Raises:
        ValidationError: If any input is invalid.
    """
    errors: list[str] = []

    if not token:
        errors.append("token is required")

    if not repository:
        errors.append("repository is required")
    elif "/" not in repository or repository.count("/") != 1:
        errors.append(f"repository must be in 'owner/repo' format, got: {repository}")

    if not check_name:
        errors.append("check_name is required")

    if not ref:
        errors.append("ref is required")
    elif not re.match(r"^[a-fA-F0-9]{40}$|^[a-zA-Z0-9._/-]+$", ref):
        # allow 40-char hex SHA or valid git ref characters
        errors.append(f"ref contains invalid characters: {ref}")

    if timeout_seconds <= 0:
        errors.append(f"timeout_seconds must be positive, got: {timeout_seconds}")

    if interval_seconds <= 0:
        errors.append(f"interval_seconds must be positive, got: {interval_seconds}")

    if interval_seconds >= timeout_seconds:
        errors.append(f"interval_seconds ({interval_seconds}) must be less than timeout_seconds ({timeout_seconds})")

    if errors:
        raise ValidationError("; ".join(errors))

    return Config(
        token=token,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        check_name=check_name,  # type: ignore[arg-type]
        ref=ref,  # type: ignore[arg-type]
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def fetch_check_runs(token: str, repository: str, ref: str) -> list[dict]:
    """Fetch check runs for a commit from GitHub API.

    Returns:
        List of check run dictionaries.

    Raises:
        URLError: If the API request fails.
    """
    url = f"https://api.github.com/repos/{repository}/commits/{ref}/check-runs"

    request = Request(url)  # noqa: S310
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "wait-for-check-action")

    with urlopen(request, timeout=30) as response:  # noqa: S310
        data = json.loads(response.read().decode("utf-8"))
        return data.get("check_runs", [])


def find_completed_check(check_runs: list[dict], check_name: str) -> str | None:
    """Find a completed check with the given name.

    Returns:
        The conclusion string if found and completed, None otherwise.
    """
    for check in check_runs:
        if check.get("name") == check_name and check.get("status") == "completed":
            return check.get("conclusion")
    return None


def wait_for_check(config: Config) -> str:
    """Poll for a check to complete.

    Returns:
        The check conclusion (e.g., "success", "failure", "timed_out").
    """
    deadline = time.time() + config.timeout_seconds

    print(f"Waiting for check '{config.check_name}' on ref {config.ref}")
    print(f"Timeout: {config.timeout_seconds}s, Interval: {config.interval_seconds}s")

    while time.time() < deadline:
        try:
            check_runs = fetch_check_runs(config.token, config.repository, config.ref)
            conclusion = find_completed_check(check_runs, config.check_name)

            if conclusion is not None:
                print(f"Check '{config.check_name}' completed with conclusion: {conclusion}")
                return conclusion

        except (HTTPError, URLError) as e:
            print(f"::warning::API request failed: {e}", file=sys.stderr)

        remaining = int(deadline - time.time())
        print(f"Check '{config.check_name}' not complete yet. {remaining}s remaining...")
        time.sleep(config.interval_seconds)

    print(f"::warning::Timed out after {config.timeout_seconds}s waiting for check '{config.check_name}'")
    return "timed_out"


def write_output(name: str, value: str) -> None:
    """Write an output to GITHUB_OUTPUT file."""
    # sanitize newlines to prevent output injection
    sanitized_value = value.replace("\r", "").replace("\n", " ")

    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:  # noqa: PTH123
            f.write(f"{name}={sanitized_value}\n")
    else:
        # fallback for local testing
        print(f"::set-output name={name}::{sanitized_value}")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Wait for a GitHub check to complete")
    parser.add_argument("--token", required=True, help="GitHub token")
    parser.add_argument("--repository", required=True, help="Repository (owner/repo)")
    parser.add_argument("--check-name", required=True, help="Name of the check to wait for")
    parser.add_argument("--ref", required=True, help="Git ref (commit SHA)")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=600,
        help="Timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=30,
        help="Polling interval in seconds (default: 30)",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """Main entry point."""
    parsed = parse_args(args)

    try:
        config = validate_config(
            token=parsed.token,
            repository=parsed.repository,
            check_name=parsed.check_name,
            ref=parsed.ref,
            timeout_seconds=parsed.timeout_seconds,
            interval_seconds=parsed.interval_seconds,
        )
    except ValidationError as e:
        print(f"::error::Validation failed: {e}")
        return 1

    conclusion = wait_for_check(config)
    write_output("conclusion", conclusion)

    return 0


if __name__ == "__main__":
    sys.exit(main())
