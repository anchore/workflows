"""Family subcommand for filtering AWS instances by runs-on.com patterns."""

from __future__ import annotations

from pathlib import Path

import click

from runson import core

from . import config, util

# sort key functions for instance lists
SORT_KEYS = {
    "price": lambda x: (x["price"] is None, x["price"] or 0),
    "spot": lambda x: (x["spot"] is None, x["spot"] or 0),
    "vcpus": lambda x: x["vcpus"],
    "memory": lambda x: x["memory_gb"],
    "api_name": lambda x: x["api_name"],
    "arch": lambda x: x["arch"],
    "ebs": lambda x: (x["ebs_mbps"] is None, x["ebs_mbps"] or 0),
}


def sort_instances(instances: list[dict], sort_by: str) -> list[dict]:
    """Sort instances by the specified column."""
    if sort_by not in SORT_KEYS:
        raise click.ClickException(f"Unknown sort column: {sort_by}. Valid options: {', '.join(SORT_KEYS.keys())}")
    return sorted(instances, key=SORT_KEYS[sort_by])


def print_table(
    instances: list[dict],
    runner_configs: dict[str, dict],
    sort_by: str,
) -> None:
    """Print formatted table of instances."""
    if not instances:
        click.echo(util.C.yellow("No matching instances found."))
        return

    sorted_instances = sort_instances(instances, sort_by)

    # pre-compute matching runners for each instance
    instance_matches = {
        inst["api_name"]: core.matching.find_matching_runners(inst, runner_configs) for inst in sorted_instances
    }

    # calculate column widths
    max_api = max(len(inst["api_name"]) for inst in sorted_instances)
    max_api = max(max_api, 8)  # minimum width

    # print header
    header = (
        f"{'API Name':<{max_api}}  "
        f"{'Price':>8}  "
        f"{'Spot':>8}  "
        f"{'Arch':<7}  "
        f"{'Memory':>8}  "
        f"{'vCPUs':>5}  "
        f"{'EBS Mbps':>10}  "
        f"Matched By"
    )
    click.echo(util.C.bold(header))
    click.echo(util.C.dim("-" * len(header.replace("Matched By", "Matched By" + " " * 20))))

    # print rows
    for inst in sorted_instances:
        matching_runners = instance_matches[inst["api_name"]]

        # format values
        api_name = f"{inst['api_name']:<{max_api}}"
        price = f"${inst['price']:.4f}" if inst["price"] else "N/A"
        spot = f"${inst['spot']:.4f}" if inst["spot"] else "N/A"
        arch = f"{inst['arch']:<7}"
        memory = f"{inst['memory_gb']:>6.0f}GB"
        vcpus = f"{inst['vcpus']:>5}"
        ebs = f"{inst['ebs_mbps']:>10}" if inst["ebs_mbps"] else "       N/A"
        matched = ", ".join(matching_runners) if matching_runners else ""

        # color based on whether matched by any runner
        if matching_runners:
            row = (
                f"{util.C.green(api_name)}  "
                f"{util.C.green(price):>8}  "
                f"{spot:>8}  "
                f"{arch}  "
                f"{memory}  "
                f"{vcpus}  "
                f"{ebs}  "
                f"{util.C.cyan(matched)}"
            )
        else:
            row = (
                f"{util.C.dim(api_name)}  "
                f"{util.C.dim(price):>8}  "
                f"{util.C.dim(spot):>8}  "
                f"{util.C.dim(arch)}  "
                f"{util.C.dim(memory)}  "
                f"{util.C.dim(vcpus)}  "
                f"{util.C.dim(ebs)}  "
                f"{matched}"
            )
        click.echo(row)

    # summary
    click.echo()
    matched_count = sum(1 for matches in instance_matches.values() if matches)
    click.echo(util.C.dim(f"Total: {len(sorted_instances)} instances ({matched_count} matched by configured runners)"))


def print_yaml(
    instances: list[dict],
    sort_by: str,
) -> None:
    """Print instances as YAML list for copy-paste into runs-on.yml."""
    if not instances:
        click.echo(util.C.yellow("No matching instances found."))
        return

    sorted_instances = sort_instances(instances, sort_by)

    # find max width for alignment (include quotes)
    max_width = max(len(inst["api_name"]) for inst in sorted_instances) + 2

    for inst in sorted_instances:
        price_str = f"${inst['price']:.4f}" if inst["price"] else "N/A"
        memory_str = f"{inst['memory_gb']:.0f}GB"
        ebs_str = f"{inst['ebs_mbps']} Mbps" if inst["ebs_mbps"] else "N/A"
        category = core.inference.get_instance_category(inst["api_name"])
        name_col = f'"{inst["api_name"]}"'.ljust(max_width)
        click.echo(
            f"- {name_col}  # {category}, {inst['arch']}, {memory_str}, {inst['vcpus']} CPU, {ebs_str}, {price_str}/hr"
        )


def print_globs(globs: list[str], instances: list[dict], sort_by: str = "price") -> None:
    """Print synthesized glob patterns as YAML with stats."""
    if not globs:
        click.echo(util.C.yellow("No patterns to display."))
        return

    # calculate min price for each glob for sorting
    def get_glob_min_price(glob: str) -> float:
        if glob.endswith("*"):
            matching = [i for i in instances if core.matching.matches_family_pattern(i["api_name"], glob)]
        else:
            matching = [i for i in instances if i["api_name"] == glob]
        prices = [i["price"] for i in matching if i.get("price") is not None]
        return min(prices) if prices else float("inf")

    # sort globs by minimum price
    if sort_by == "price":
        globs = sorted(globs, key=get_glob_min_price)

    # find max width for alignment
    max_width = max(len(g) for g in globs) + 2  # +2 for quotes

    for glob in globs:
        # find matching instances from universe
        if glob.endswith("*"):
            matching = [i for i in instances if core.matching.matches_family_pattern(i["api_name"], glob)]
        else:
            matching = [i for i in instances if i["api_name"] == glob]

        if not matching:
            glob_col = f'"{glob}"'.ljust(max_width)
            click.echo(f"- {glob_col}  # unknown")
            continue

        # category
        category = core.inference.get_instance_category(matching[0]["api_name"])

        # architectures
        arches = sorted({i["arch"] for i in matching})
        arch_str = "/".join(arches)

        # cpu range
        cpus = [i["vcpus"] for i in matching]
        cpu_str = util.format_range(cpus, " CPU")

        # memory range
        mems = [int(i["memory_gb"]) for i in matching]
        mem_str = util.format_range(mems, "GB")

        # ebs bandwidth range
        ebs_vals = [i["ebs_mbps"] for i in matching if i["ebs_mbps"] is not None]
        ebs_str = util.format_range(ebs_vals, " Mbps") if ebs_vals else "N/A"

        # price range
        prices = [i["price"] for i in matching if i["price"] is not None]
        if prices:
            min_p, max_p = min(prices), max(prices)
            price_str = f"${min_p:.2f}/hr" if min_p == max_p else f"${min_p:.2f}-${max_p:.2f}/hr"
        else:
            price_str = "N/A"

        glob_col = f'"{glob}"'.ljust(max_width)
        click.echo(f"- {glob_col}  # {category}, {arch_str}, {cpu_str}, {mem_str}, {ebs_str}, {price_str}")


def format_req(name: str, req: config.Requirement) -> str:
    """Format a requirement for display."""
    if req.max_val is None:
        return f"{name}={req.min_val}"
    return f"{name}={req.min_val}:{req.max_val}"


@click.command()
@click.argument("selectors", nargs=-1)
@click.option(
    "--sort",
    default="price",
    help="Sort by column: price, spot, vcpus, memory, api_name, arch, ebs",
)
@click.option("--cpu", type=str, help="vCPU requirement: exact ('8') or range ('8:16')")
@click.option("--mem", type=str, help="RAM requirement in GB: exact ('16') or range ('16:32')")
@click.option("--runner", help="Use specific runner's config from runs-on.yml (family + cpu + ram)")
@click.option(
    "-o",
    "--output",
    type=click.Choice(["list", "yaml"]),
    default="list",
    help="Output format: list (table) or yaml (for copy-paste)",
)
@click.option(
    "--pick-family",
    is_flag=True,
    help="Ignore family restrictions, show all instances matching cpu/ram (use with --runner)",
)
@click.option(
    "--arch",
    "arches",
    multiple=True,
    help="Filter by architecture (arm64, amd64, x86_64). Can be specified multiple times.",
)
@click.option(
    "--budget",
    type=float,
    metavar="PRICE",
    help="Only show instances below this hourly price (on-demand)",
)
@click.option("--ebs-min", type=int, metavar="MBPS", help="Minimum EBS bandwidth in Mbps")
@click.option(
    "--for-tmpfs",
    is_flag=True,
    help="Filter for tmpfs-suitable instances (prioritize memory-optimized)",
)
@click.option(
    "--globs",
    is_flag=True,
    help="Output minimal glob patterns that select exactly the filtered instances",
)
@click.option(
    "-c",
    "--config",
    "cfg",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to runs-on.yml config file (default: .github/runs-on.yml)",
)
def family(
    selectors: tuple[str, ...],
    sort: str,
    cpu: str | None,
    mem: str | None,
    runner: str | None,
    output: str,
    pick_family: bool,
    arches: tuple[str, ...],
    budget: float | None,
    ebs_min: int | None,
    for_tmpfs: bool,
    globs: bool,
    cfg: Path | None,
) -> None:
    """Filter AWS instances by runs-on.com family selector patterns.

    Shows matching instances sorted by cost with architecture and runner annotations.

    \b
    Examples:
      runson family "r8*"                         # Filter by r8 family
      runson family "r8*" "m7*"                   # Filter by multiple families
      runson family "r8*" --cpu=8 --ram=16        # Exact: 8 CPUs, 16GB RAM
      runson family "r8*" --cpu=8:16 --ram=16:32  # Range: 8-16 CPUs, 16-32GB RAM
      runson family --runner=grype-db-pull        # Use specific runner's config
      runson family --runner=grype-db-pull -o yaml  # Output as YAML list
      runson family                               # Use all from runs-on.yml
      runson family --sort=spot                   # Sort by spot price
    """
    # determine config file path
    if cfg:
        runson_path = cfg
    else:
        cwd = Path.cwd()
        repo_root = config.find_repo_root(cwd)
        runson_path = repo_root / ".github" / "runs-on.yml"

    # load runner configs for annotation and filtering
    runner_configs: dict[str, dict] = {}
    if runson_path.exists():
        runner_configs = config.load_runner_configs(runson_path)
        if cfg:
            click.echo(util.C.dim(f"Loaded {len(runner_configs)} runners from {runson_path}"))
        else:
            click.echo(util.C.dim(f"Loaded {len(runner_configs)} runners from {runson_path.relative_to(repo_root)}"))

    # determine patterns and cpu/ram requirements
    cpu_req: config.Requirement | None = None
    ram_req: config.Requirement | None = None
    patterns: list[str] | None = None

    # parse CLI requirements if provided
    cli_cpu = None
    cli_ram = None
    try:
        if cpu:
            cli_cpu = config.parse_cli_requirement(cpu)
        if mem:
            cli_ram = config.parse_cli_requirement(mem)
    except ValueError as e:
        raise click.ClickException(str(e)) from None

    if pick_family:
        # pick-family mode: ignore all family restrictions, filter by cpu/ram only
        patterns = None
        cpu_req = cli_cpu
        ram_req = cli_ram
        if runner:
            # use runner's cpu/ram as defaults if not overridden by CLI
            if runner not in runner_configs:
                raise click.ClickException(
                    f"Runner '{runner}' not found in runs-on.yml. Available runners: {', '.join(runner_configs.keys())}"
                )
            runner_config = runner_configs[runner]
            if cpu_req is None:
                cpu_req = runner_config["cpu"]
            if ram_req is None:
                ram_req = runner_config["ram"]
            click.echo(util.C.dim(f"Using runner: {runner}"))
        click.echo(util.C.dim("Pick-family mode: showing all families"))
        reqs = []
        if cpu_req:
            reqs.append(format_req("cpu", cpu_req))
        if ram_req:
            reqs.append(format_req("ram", ram_req) + "GB")
        if reqs:
            click.echo(util.C.dim(f"config.Requirements: {', '.join(reqs)}"))
    elif runner:
        # use specific runner's config
        if runner not in runner_configs:
            raise click.ClickException(
                f"Runner '{runner}' not found in runs-on.yml. Available runners: {', '.join(runner_configs.keys())}"
            )
        runner_config = runner_configs[runner]
        cpu_req = runner_config["cpu"]
        ram_req = runner_config["ram"]
        patterns = runner_config["families"]
        click.echo(util.C.dim(f"Using runner: {runner}"))
        reqs = []
        if cpu_req:
            reqs.append(format_req("cpu", cpu_req))
        if ram_req:
            reqs.append(format_req("ram", ram_req) + "GB")
        if reqs:
            click.echo(util.C.dim(f"config.Requirements: {', '.join(reqs)}"))
    elif selectors:
        patterns = list(selectors)
        cpu_req = cli_cpu
        ram_req = cli_ram
        if cpu_req or ram_req:
            reqs = []
            if cpu_req:
                reqs.append(format_req("cpu", cpu_req))
            if ram_req:
                reqs.append(format_req("ram", ram_req) + "GB")
            click.echo(util.C.dim(f"config.Requirements: {', '.join(reqs)}"))
    elif runner_configs:
        # collect all unique patterns from all runners
        all_patterns: set[str] = set()
        for rc in runner_configs.values():
            all_patterns.update(rc["families"])
        patterns = list(all_patterns)
        cpu_req = cli_cpu
        ram_req = cli_ram
        click.echo(util.C.dim(f"Using {len(patterns)} patterns from configured runners"))
        if cpu_req or ram_req:
            reqs = []
            if cpu_req:
                reqs.append(format_req("cpu", cpu_req))
            if ram_req:
                reqs.append(format_req("ram", ram_req) + "GB")
            click.echo(util.C.dim(f"Requirements: {', '.join(reqs)}"))
        else:
            click.echo(util.C.dim("(cpu/ram requirements vary by runner)"))
    else:
        raise click.ClickException("No selectors provided and no runs-on.yml found")

    if patterns:
        click.echo(util.C.dim(f"Patterns: {', '.join(patterns)}"))

    # handle --for-tmpfs: set minimum RAM for tmpfs-suitable instances
    if for_tmpfs:
        if ram_req is None:
            # set minimum 32GB for tmpfs (max=99999 effectively means "32GB or more")
            ram_req = config.Requirement(min_val=32, max_val=99999)
            click.echo(util.C.dim("tmpfs mode: filtering for 32GB+ RAM instances"))
        else:
            click.echo(util.C.dim("tmpfs mode: using specified RAM requirement"))
    click.echo()

    # load and filter instances
    instances = config.load_instances()

    arches_list = list(arches) if arches else None
    filtered = core.matching.filter_instances(
        instances,
        patterns,
        cpu=cpu_req,
        ram=ram_req,
        arches=arches_list,
        max_price=budget,
        ebs_min=ebs_min,
    )

    # print results
    if globs:
        glob_patterns = core.synthesis.synthesize_globs(filtered, filtered, budget)
        print_globs(glob_patterns, filtered, sort)
    elif output == "yaml":
        print_yaml(filtered, sort)
    else:
        # only show specified runner in "Matched By" if --runner used
        if runner:
            display_configs = {runner: runner_configs[runner]}
        else:
            display_configs = runner_configs
        print_table(filtered, display_configs, sort)
