"""Estimate subcommand for calculating GitHub Actions workflow run costs."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import click
import yaml

from runson import core

from . import config, util


def parse_workflow_url(url_or_id: str) -> tuple[str, str, bool]:
    """Parse workflow run URL or ID and return (owner/repo, run_id, is_remote_url).

    is_remote_url is True when a full URL was provided, False when just a run ID.
    """
    # check if it's just a run ID
    if url_or_id.isdigit():
        # need to get the repo from local git config
        cwd = Path.cwd()
        repo_root = config.find_repo_root(cwd)
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True,
                cwd=repo_root,
            )
            remote_url = result.stdout.strip()
            # parse owner/repo from various URL formats
            match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", remote_url)
            if match:
                return match.group(1).rstrip(".git"), url_or_id, False
        except subprocess.CalledProcessError:
            pass
        raise click.ClickException("Could not determine repository from git remote")

    # parse full URL
    match = re.search(r"github\.com/([^/]+/[^/]+)/actions/runs/(\d+)", url_or_id)
    if match:
        return match.group(1), match.group(2), True

    raise click.ClickException(f"Invalid workflow run URL or ID: {url_or_id}")


def fetch_remote_config(repo: str) -> dict[str, dict]:
    """Fetch runs-on.yml from a GitHub repository via API.

    Returns parsed runner configs, or empty dict if not found.
    """
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/.github/runs-on.yml"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        # content is base64 encoded
        content = base64.b64decode(data["content"]).decode("utf-8")
        config_data = yaml.safe_load(content)
        return config.parse_runner_configs_from_data(config_data)
    except subprocess.CalledProcessError:
        return {}
    except (json.JSONDecodeError, KeyError, yaml.YAMLError):
        return {}


def fetch_workflow_run(repo: str, run_id: str) -> dict:  # type: ignore[type-arg]
    """Fetch workflow run data from GitHub API."""
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/actions/runs/{run_id}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Failed to fetch workflow run: {e.stderr}") from None


def fetch_workflow_jobs(repo: str, run_id: str) -> list[dict]:
    """Fetch all jobs for a workflow run, handling pagination."""
    jobs = []
    page = 1
    per_page = 100

    while True:
        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/{repo}/actions/runs/{run_id}/jobs?per_page={per_page}&page={page}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)
            jobs.extend(data.get("jobs", []))

            # check if we got all jobs
            if len(data.get("jobs", [])) < per_page:
                break
            page += 1
        except subprocess.CalledProcessError as e:
            raise click.ClickException(f"Failed to fetch workflow jobs: {e.stderr}") from None

    return jobs


def parse_inline_runner_spec(label: str) -> dict | None:
    """Parse inline runner spec params from a runs-on label.

    Returns a dict with any found pricing params ('families', 'cpu', 'ram', 'spot'),
    or None if no pricing params found. Can be used standalone or as overrides
    to a named runner config.
    """
    if not label.startswith("runs-on="):
        return None

    # split by / or , to get key=value pairs
    parts = re.split(r"[/,]", label)

    result: dict = {}

    for part in parts:
        if "=" not in part:
            continue

        key, value = part.split("=", 1)
        key = key.strip().lower()

        if key == "cpu":
            # parse cpu=8 or cpu=8+32
            if "+" in value:
                min_val, max_val = value.split("+", 1)
                result["cpu"] = config.Requirement(min_val=int(min_val), max_val=int(max_val))
            else:
                result["cpu"] = config.Requirement(min_val=int(value))

        elif key == "ram":
            # parse ram=16 or ram=16+64
            if "+" in value:
                min_val, max_val = value.split("+", 1)
                result["ram"] = config.Requirement(min_val=int(min_val), max_val=int(max_val))
            else:
                result["ram"] = config.Requirement(min_val=int(value))

        elif key == "family":
            # parse family=m4+m5+m6 into list with wildcards
            families = []
            for fam in value.split("+"):
                fam = fam.strip()
                if not fam:
                    continue
                # add wildcard if not already present and not an exact instance name
                if "." not in fam and not fam.endswith("*"):
                    fam = f"{fam}*"
                families.append(fam)
            if families:
                result["families"] = families

        elif key == "spot":
            # parse spot=true/false or strategy name
            if value.lower() == "false":
                result["spot"] = False
            elif value.lower() == "true":
                result["spot"] = True
            else:
                # strategy like "price-capacity-optimized"
                result["spot"] = value

    return result if result else None


def parse_runner_from_labels(labels: list[str]) -> tuple[str | None, dict | None, bool]:
    """Extract runner info from job labels.

    Returns (runner_name, inline_overrides, is_runs_on).
    - runner_name: Named runner if /runner=<name> present, else None
    - inline_overrides: Dict with inline params (cpu, ram, family, spot), else None
    - is_runs_on: True if this is a runs-on runner
    """
    runner_name = None
    inline_overrides = None

    for label in labels:
        if not label.startswith("runs-on="):
            continue

        # extract runner name if present
        match = re.search(r"runner=([^/,]+)", label)
        if match:
            runner_name = match.group(1)

        # parse inline specs (can coexist with runner name as overrides)
        inline_overrides = parse_inline_runner_spec(label)

        return runner_name, inline_overrides, True

    # check for GitHub-hosted runners (standard and larger)
    for label in labels:
        label_lower = label.lower()
        # standard runners: ubuntu-24.04, windows-latest, macos-latest
        if label_lower.startswith("ubuntu") or label_lower.startswith("linux"):
            return label, None, False
        if label_lower.startswith("windows"):
            return label, None, False
        if label_lower.startswith("macos"):
            return label, None, False

    return None, None, False


def merge_runner_config(base: dict, overrides: dict) -> dict:
    """Merge inline overrides into a base runner config.

    Inline values override base values. For families, overrides completely replace
    (not append to) the base list.
    """
    result = base.copy()
    if "families" in overrides:
        result["families"] = overrides["families"]
    if "cpu" in overrides:
        result["cpu"] = overrides["cpu"]
    if "ram" in overrides:
        result["ram"] = overrides["ram"]
    if "spot" in overrides:
        result["spot"] = overrides["spot"]
    return result


def format_inline_spec(cfg: dict) -> str:
    """Format inline config for display in output."""
    parts = []
    if "cpu" in cfg:
        cpu = cfg["cpu"]
        if cpu.max_val is not None:
            parts.append(f"cpu={cpu.min_val}-{cpu.max_val}")
        else:
            parts.append(f"cpu={cpu.min_val}")
    if "ram" in cfg:
        ram = cfg["ram"]
        if ram.max_val is not None:
            parts.append(f"ram={ram.min_val}-{ram.max_val}")
        else:
            parts.append(f"ram={ram.min_val}")
    if "families" in cfg:
        # show first few families
        fams = cfg["families"]
        if len(fams) <= 3:
            parts.append(f"family={'+'.join(fams)}")
        else:
            parts.append(f"family={'+'.join(fams[:2])}+...")
    return "/".join(parts) if parts else "inline"


def parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO timestamp from GitHub API."""
    if not ts:
        return None
    # handle format: 2025-12-26T21:10:31Z
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_larger_runner_label(label: str) -> tuple[str | None, str | None, int | None]:
    """Parse a GitHub larger runner label to extract OS, architecture, and cores.

    Handles formats like:
    - Linux_x64_8Core_32gbRam_300gbSSD
    - Windows_x64_16Core_64gbRam
    - ubuntu-24.04-arm64-4-core
    - ubuntu-latest-16-cores

    Returns (os, arch, cores) or (None, None, None) if not a larger runner.
    """
    label_lower = label.lower()

    # try format: Linux_x64_8Core_... or Windows_x64_16Core_...
    match = re.match(r"(linux|windows|macos)[_-](x64|arm64)[_-](\d+)core", label_lower)
    if match:
        os_name = match.group(1)
        arch = match.group(2)
        cores = int(match.group(3))
        return os_name, arch, cores

    # try format: ubuntu-24.04-arm64-4-core or ubuntu-latest-16-cores
    match = re.match(r"(ubuntu|windows|macos)[^-]*-.*?-?(arm64|x64)?-?(\d+)-?cores?", label_lower)
    if match:
        os_name = "linux" if match.group(1) == "ubuntu" else match.group(1)
        arch = match.group(2) if match.group(2) else "x64"
        cores = int(match.group(3))
        return os_name, arch, cores

    # try format: ubuntu-latest-16-cores (no arch specified)
    match = re.match(r"(ubuntu|linux)[^-]*-.*?(\d+)-?cores?", label_lower)
    if match:
        cores = int(match.group(2))
        return "linux", "x64", cores

    return None, None, None


def get_github_hosted_cost(label: str, duration_minutes: float) -> tuple[float, str]:
    """Calculate cost for GitHub-hosted runner.

    Returns (cost, detail_string) where detail_string describes the pricing.
    """
    # try to parse as a larger runner first
    os_name, arch, cores = parse_larger_runner_label(label)

    if os_name and arch and cores:
        pricing_table = config.GITHUB_LARGER_RUNNER_PRICING.get((os_name, arch), {})
        per_minute = pricing_table.get(cores)

        if per_minute:
            cost = duration_minutes * per_minute
            detail = f"{cores}-core {arch} @ ${per_minute:.3f}/min"
            return cost, detail

        # cores not in table, try to find closest or extrapolate
        if pricing_table:
            # find the closest core count
            available_cores = sorted(pricing_table.keys())
            closest = min(available_cores, key=lambda x: abs(x - cores))
            per_minute = pricing_table[closest]
            cost = duration_minutes * per_minute
            detail = f"{cores}-core {arch} (~{closest}-core @ ${per_minute:.3f}/min)"
            return cost, detail

    # fall back to standard runner pricing
    label_lower = label.lower()
    if "ubuntu" in label_lower or "linux" in label_lower:
        per_minute = config.GITHUB_STANDARD_PRICING["linux"]
        cost = duration_minutes * per_minute
        return cost, "GitHub-hosted"
    if "windows" in label_lower:
        per_minute = config.GITHUB_STANDARD_PRICING["windows"]
        cost = duration_minutes * per_minute
        return cost, "GitHub-hosted"
    if "macos" in label_lower:
        per_minute = config.GITHUB_STANDARD_PRICING["macos"]
        cost = duration_minutes * per_minute
        return cost, "GitHub-hosted"

    # default to Linux pricing
    per_minute = config.GITHUB_STANDARD_PRICING["linux"]
    cost = duration_minutes * per_minute
    return cost, "GitHub-hosted"


@click.command()
@click.argument("run")
@click.option(
    "-c",
    "--config",
    "cfg",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to local runs-on.yml config file",
)
@click.option(
    "--no-fetch-config",
    is_flag=True,
    help="Don't auto-fetch runs-on.yml from target repo (use local config instead)",
)
def estimate(run: str, cfg: Path | None, no_fetch_config: bool) -> None:
    """Estimate the cost of a GitHub Actions workflow run.

    RUN can be a workflow run URL or just the run ID.

    When a full GitHub URL is provided, the command automatically fetches
    runs-on.yml from that repository. Use --no-fetch-config to use the
    local repository's config instead.

    \b
    Examples:
      runson estimate https://github.com/owner/repo/actions/runs/123
      runson estimate 123  # Uses current repo and local config
      runson estimate URL --no-fetch-config  # Use local config for remote workflow
      runson estimate 123 --config=/path/to/runs-on.yml
    """
    # parse input and determine if URL was provided
    repo, run_id, is_remote_url = parse_workflow_url(run)

    # load instance pricing data
    instances = config.load_instances()

    # load runner configs based on options
    runner_configs: dict[str, dict] = {}
    if cfg:
        # explicit local config file (highest priority)
        runner_configs = config.load_runner_configs(cfg)
        click.echo(util.C.dim(f"Loaded config from {cfg}"))
    elif is_remote_url and not no_fetch_config:
        # auto-fetch from target repo when URL provided
        click.echo(util.C.dim(f"Fetching runs-on.yml from {repo}..."))
        runner_configs = fetch_remote_config(repo)
        if runner_configs:
            click.echo(util.C.dim(f"Loaded {len(runner_configs)} runners from {repo}"))
        else:
            click.echo(util.C.yellow(f"No runs-on.yml found in {repo}"))
    else:
        # default: local repo config (for run ID or --no-fetch-config)
        cwd = Path.cwd()
        repo_root = config.find_repo_root(cwd)
        runson_path = repo_root / ".github" / "runs-on.yml"
        if runson_path.exists():
            runner_configs = config.load_runner_configs(runson_path)
            click.echo(util.C.dim(f"Using local config from {runson_path.relative_to(repo_root)}"))

    # fetch workflow data
    click.echo(util.C.dim(f"Fetching workflow run {run_id} from {repo}..."))
    run_data = fetch_workflow_run(repo, run_id)
    jobs = fetch_workflow_jobs(repo, run_id)

    # display header
    workflow_name = run_data.get("name", "Unknown")
    run_number = run_data.get("run_number", "?")
    status = run_data.get("status", "unknown")
    conclusion = run_data.get("conclusion")

    click.echo()
    click.echo(util.C.bold("━" * 80))
    click.echo(f"  {util.C.bold(workflow_name)} {util.C.dim(f'(#{run_number})')}")
    click.echo(util.C.bold("━" * 80))

    status_str = conclusion if conclusion else status
    if status_str == "success":
        click.echo(f"  Status: {util.C.green(status_str)}")
    elif status_str == "failure":
        click.echo(f"  Status: {util.C.red(status_str)}")
    elif status_str == "in_progress":
        click.echo(f"  Status: {util.C.yellow(status_str)}")
    else:
        click.echo(f"  Status: {status_str}")

    # calculate total duration
    run_start = parse_timestamp(run_data.get("run_started_at"))
    run_updated = parse_timestamp(run_data.get("updated_at"))
    if run_start and run_updated:
        total_duration = (run_updated - run_start).total_seconds()
        click.echo(f"  Duration: {util.format_duration(total_duration)}")

    # process jobs
    click.echo(f"\n{util.C.cyan('Jobs:')}")

    total_lower = 0.0
    total_upper = 0.0

    for job in jobs:
        job_name = job.get("name", "Unknown")
        job_conclusion = job.get("conclusion")
        labels = job.get("labels", [])

        started = parse_timestamp(job.get("started_at"))
        completed = parse_timestamp(job.get("completed_at"))

        # calculate duration
        if started and completed:
            duration_secs = (completed - started).total_seconds()
            duration_mins = duration_secs / 60.0
            duration_str = util.format_duration(duration_secs)
        elif started:
            # in progress
            now = datetime.now(started.tzinfo)
            duration_secs = (now - started).total_seconds()
            duration_mins = duration_secs / 60.0
            duration_str = f"{util.format_duration(duration_secs)}+"
        else:
            duration_mins = 0
            duration_str = "-"

        # determine runner and cost
        runner_name, inline_overrides, is_runs_on = parse_runner_from_labels(labels)

        if is_runs_on:
            # start with named runner config as base (or empty dict)
            if runner_name:
                runner_config = runner_configs.get(runner_name, {}).copy()
                runner_display_name = runner_name
            else:
                runner_config = {}
                runner_display_name = None

            # apply inline overrides
            if inline_overrides:
                runner_config = merge_runner_config(runner_config, inline_overrides)
                if runner_display_name:
                    runner_display_name = f"{runner_name}*"
                else:
                    runner_display_name = format_inline_spec(inline_overrides)

            min_price, max_price, min_inst, _ = core.matching.get_runner_price_range(runner_config, instances)

            if min_price is not None and max_price is not None:
                duration_hours = duration_mins / 60.0
                cost_lower = min_price * duration_hours
                cost_upper = max_price * duration_hours
                total_lower += cost_lower
                total_upper += cost_upper

                cost_str = f"${cost_lower:.3f}"
                use_spot = core.matching._is_spot_enabled(runner_config.get("spot"))
                spot_indicator = "spot, " if use_spot else ""
                detail = f"{spot_indicator}{min_inst} @ ${min_price:.2f}/hr"
                runner_display = util.C.cyan(runner_display_name or "inline")
            else:
                cost_str = "?"
                detail = "no pricing data"
                runner_display = util.C.yellow(runner_display_name or "inline")
        elif runner_name:
            # GitHub-hosted (including larger runners)
            cost, detail = get_github_hosted_cost(runner_name, duration_mins)
            total_lower += cost
            total_upper += cost
            cost_str = f"${cost:.3f}"
            runner_display = util.C.dim(runner_name)
        else:
            cost_str = "-"
            detail = ""
            runner_display = util.C.dim("unknown")

        # format job status
        job_status_display = job_conclusion if job_conclusion else job.get("status", "unknown")
        if job_status_display == "success":
            status_icon = util.C.green("✓")
        elif job_status_display == "failure":
            status_icon = util.C.red("✗")
        elif job_status_display == "in_progress":
            status_icon = util.C.yellow("●")
        elif job_status_display == "queued":
            status_icon = util.C.dim("○")
        else:
            status_icon = util.C.dim("?")

        # print job line
        name_col = f"{job_name[:40]:<40}"
        duration_col = f"{duration_str:>10}"

        if detail:
            detail_str = util.C.dim(f"({detail})")
            cost_col = util.C.green(cost_str)
            click.echo(f"  {status_icon} {name_col} {duration_col}  {runner_display:<25} {cost_col:>8} {detail_str}")
        else:
            click.echo(f"  {status_icon} {name_col} {duration_col}  {runner_display:<25} {cost_str:>8}")

    # summary
    click.echo()
    click.echo(util.C.bold("━" * 80))
    click.echo(f"  {util.C.bold('TOTAL COST ESTIMATE')}")
    click.echo(util.C.bold("━" * 80))

    click.echo(f"\n  {util.C.bold(util.C.green(f'${total_lower:.2f}'))} {util.C.dim('(spot pricing)')}")
    if total_lower != total_upper:
        click.echo(f"  {util.C.dim(f'${total_upper:.2f} (on-demand fallback)')}")
    click.echo()
