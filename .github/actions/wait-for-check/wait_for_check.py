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

# github caps check-runs responses at 100 per page
PER_PAGE = 100

# safety bound on pagination so a misbehaving API can't loop forever
MAX_PAGES = 10


class Config(NamedTuple):
    """Validated configuration for the wait operation."""

    token: str
    repository: str
    check_name: str
    ref: str
    timeout_seconds: int
    interval_seconds: int
    # how long to wait for a check with the given name to appear at all (any
    # status) before giving up early. distinct from timeout_seconds, which
    # bounds how long we wait for an already-seen check to complete.
    not_found_timeout_seconds: int = 60
    verbose: bool = False


class ValidationError(Exception):
    """Raised when input validation fails."""


def validate_config(
    token: str | None,
    repository: str | None,
    check_name: str | None,
    ref: str | None,
    timeout_seconds: int,
    interval_seconds: int,
    not_found_timeout_seconds: int = 60,
    verbose: bool = False,
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

    if not_found_timeout_seconds <= 0:
        errors.append(f"not_found_timeout_seconds must be positive, got: {not_found_timeout_seconds}")
    elif not_found_timeout_seconds >= timeout_seconds:
        # otherwise the early give-up never fires - the full timeout is reached first
        errors.append(
            f"not_found_timeout_seconds ({not_found_timeout_seconds}) must be less than "
            f"timeout_seconds ({timeout_seconds})"
        )

    if errors:
        raise ValidationError("; ".join(errors))

    return Config(
        token=token,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        check_name=check_name,  # type: ignore[arg-type]
        ref=ref,  # type: ignore[arg-type]
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        not_found_timeout_seconds=not_found_timeout_seconds,
        verbose=verbose,
    )


def fetch_check_runs(token: str, repository: str, ref: str) -> list[dict]:
    """Fetch all check runs for a commit from GitHub API, following pagination.

    Returns:
        List of check run dictionaries across all pages.

    Raises:
        URLError: If the API request fails.
    """
    runs: list[dict] = []

    for page in range(1, MAX_PAGES + 1):
        url = f"https://api.github.com/repos/{repository}/commits/{ref}/check-runs?per_page={PER_PAGE}&page={page}"

        request = Request(url)  # noqa: S310
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "wait-for-check-action")

        with urlopen(request, timeout=30) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))

        batch = data.get("check_runs", [])
        runs.extend(batch)

        total = data.get("total_count", 0)
        # stop once we've drained a short page or collected everything reported
        if not batch or len(batch) < PER_PAGE or len(runs) >= total:
            break
    else:
        # loop ran the full range without breaking: we hit the page cap and
        # may not have examined every check run on the ref
        print(
            f"::warning::Reached pagination cap of {MAX_PAGES} pages "
            f"({MAX_PAGES * PER_PAGE} check runs); some check runs may not have been examined.",
        )

    return runs


def _normalize(name: str) -> str:
    """Fold case and trim whitespace so trivial name differences still match."""
    return name.strip().casefold()


def find_matching_checks(check_runs: list[dict], check_name: str) -> list[dict]:
    """Return all check runs whose name matches check_name.

    Matching is case-insensitive and whitespace-trimmed, but otherwise a full
    name match (no partial/substring matching).
    """
    target = _normalize(check_name)
    return [c for c in check_runs if _normalize(c.get("name") or "") == target]


def resolve_conclusion(matches: list[dict]) -> str | None:
    """Resolve a final conclusion from matching check runs.

    Fails closed for duplicates: if multiple checks share the name, every one
    must complete successfully. Returns None while any matching run is still
    pending (we wait for the full set before deciding).

    What counts as a "duplicate" here is worth being precise about, because it
    determines whether this fail-closed branch ever fires:

    - NOT re-runs / repeated attempts of the same job. The check-runs-for-ref
      endpoint defaults to filter=latest, which collapses superseded attempts
      within a check suite, so a re-run replaces (not duplicates) the prior run.
    - IS two *different* workflows that each define a job with the same name on
      the same commit. GitHub Actions creates one check suite per workflow run
      (a single commit routinely has several Actions suites), and filter=latest
      dedupes within a suite, not globally by name across the ref - so both
      same-named runs are returned.
    - IS a same-named check posted by a *different* app (e.g. a status-check bot
      or external CI), which lives in its own suite and is likewise returned.

    For a single repo today this is usually defensive rather than load-bearing:
    job names are unique within a workflow, so duplicates only appear once a
    second workflow (or app) reuses a gated check's name. Treating the worst
    matching conclusion as the verdict keeps a name collision from silently
    letting a failure through the gate.

    Returns:
        The conclusion string once all matching runs complete, None otherwise.
    """
    # no matches means nothing has resolved yet - never report success here
    if not matches:
        return None

    if any(c.get("status") != "completed" for c in matches):
        return None

    # all matching runs completed: succeed only if every one succeeded,
    # otherwise surface a non-success conclusion so the gate fails
    for c in matches:
        if c.get("conclusion") != "success":
            return c.get("conclusion") or "failure"
    return "success"


def _seen_names(check_runs: list[dict]) -> list[str]:
    """Return the sorted, de-duplicated set of check-run names seen."""
    return sorted({(c.get("name") or "") for c in check_runs})


def wait_for_check(config: Config) -> str:
    """Poll for a check to complete.

    Returns:
        The check conclusion (e.g., "success", "failure", "timed_out", "not_found").
    """
    start = time.time()
    deadline = start + config.timeout_seconds
    ever_seen = False
    ever_fetched = False
    last_error: Exception | None = None
    last_seen_names: list[str] = []

    print(f"Waiting for check '{config.check_name}' on ref {config.ref}")
    print(
        f"Timeout: {config.timeout_seconds}s, not-found timeout: "
        f"{config.not_found_timeout_seconds}s, interval: {config.interval_seconds}s",
    )

    while time.time() < deadline:
        try:
            check_runs = fetch_check_runs(config.token, config.repository, config.ref)
            ever_fetched = True
            last_error = None
            last_seen_names = _seen_names(check_runs)

            if config.verbose:
                print(f"Saw {len(check_runs)} check run(s) on ref: {', '.join(last_seen_names) or '(none)'}")

            matches = find_matching_checks(check_runs, config.check_name)
            if matches:
                ever_seen = True
                conclusion = resolve_conclusion(matches)
                if conclusion is not None:
                    print(f"Check '{config.check_name}' completed with conclusion: {conclusion}")
                    return conclusion

        except (HTTPError, URLError) as e:
            last_error = e
            print(f"::warning::API request failed: {e}", file=sys.stderr)

        # give up early if the check name never shows up at all - this usually
        # means a name mismatch (or an unreachable API) rather than a slow check
        if not ever_seen and (time.time() - start) >= config.not_found_timeout_seconds:
            _report_check_never_seen(
                config,
                ever_fetched=ever_fetched,
                last_seen_names=last_seen_names,
                last_error=last_error,
                elapsed_label=f"after {config.not_found_timeout_seconds}s",
            )
            return "not_found"

        remaining = int(deadline - time.time())
        state = "seen, waiting to complete" if ever_seen else "not found yet"
        print(f"Check '{config.check_name}' {state}. {remaining}s remaining...")
        time.sleep(config.interval_seconds)

    # full timeout reached
    if ever_seen:
        print(
            f"::warning::Timed out after {config.timeout_seconds}s: check "
            f"'{config.check_name}' was found but never reached a completed state.",
        )
    else:
        _report_check_never_seen(
            config,
            ever_fetched=ever_fetched,
            last_seen_names=last_seen_names,
            last_error=last_error,
            elapsed_label=f"after the full {config.timeout_seconds}s timeout",
        )
    return "timed_out"


def _report_check_never_seen(
    config: Config,
    *,
    ever_fetched: bool,
    last_seen_names: list[str],
    last_error: Exception | None,
    elapsed_label: str,
) -> None:
    """Explain why a named check never appeared, distinguishing the likely cause.

    If we never managed a single successful API call, the check name says nothing -
    the real problem is connectivity. Otherwise it almost always means the name does
    not match any check run on the ref.
    """
    if not ever_fetched and last_error is not None:
        print(
            f"::error::Gave up {elapsed_label} on check '{config.check_name}' (ref {config.ref}): "
            f"every GitHub API request failed, so check status could not be determined "
            f"(last error: {last_error}).",
        )
        return

    print(
        f"::error::Check '{config.check_name}' was not found on ref {config.ref} {elapsed_label}. "
        f"This usually means the check name does not match any check run (typo, wrong "
        f"workflow, or the check never started).",
    )
    _report_available_checks(last_seen_names)


def _report_available_checks(names: list[str]) -> None:
    """Log the check names that were actually observed, to aid debugging mismatches."""
    if not names:
        print("::warning::No check runs were observed on the ref.")
        return
    print(f"Check runs observed on the ref ({len(names)}):")
    for name in names:
        print(f"  - {name}")


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
    parser.add_argument(
        "--not-found-timeout-seconds",
        type=int,
        default=60,
        help="Time to wait for the check to appear at all before giving up (default: 60)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log the check-run names seen on each poll",
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
            not_found_timeout_seconds=parsed.not_found_timeout_seconds,
            verbose=parsed.verbose,
        )
    except ValidationError as e:
        print(f"::error::Validation failed: {e}")
        return 1

    conclusion = wait_for_check(config)
    write_output("conclusion", conclusion)

    return 0


if __name__ == "__main__":
    sys.exit(main())
