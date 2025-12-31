# runson

Tools for analyzing and estimating costs for [runs-on.com](https://runs-on.com) runners.

**Use cases:**
1. **Discover families** - Given CPU/RAM requirements, find which family patterns to use
2. **Inspect configuration** - See what instances your current runner config selects
4. **Analyze workflow runs** - Estimate the cost of a specific workflow run
5. **Plan tmpfs usage** - Find memory-optimized instances suitable for RAM-based storage

---

## Installation

```bash
pip install .
# or with uv
uv pip install .
```

---

## Matching Rules

runs-on.com uses specific matching rules when selecting EC2 instances:

| Field | Matching | Example |
|-------|----------|---------|
| **cpu** | Exact | `cpu: 8` matches only 8-vCPU instances |
| **ram** | Exact | `ram: 16` matches only 16GB instances |
| **family** | Prefix | `r8*` matches r8g, r8i, r8a, r8gd, etc. |

For ranges, use a list: `cpu: [4, 16]` matches 4-16 vCPUs, `ram: [16, 64]` matches 16-64GB.

---

## runson family

Filter AWS instances by runs-on.com family selector patterns. Shows matching instances with pricing, architecture, and runner annotations.

### Usage

```bash
# 1. DISCOVER FAMILIES - Find family patterns for given CPU/RAM requirements
runson family --cpu=8 --mem=32 --pick-family --globs
runson family --cpu=8 --mem=32 --arch=arm64 --pick-family --globs

# 2. INSPECT CONFIG - See what instances a runner's config selects
runson family --runner=grype-db-build
runson family --runner=grype-db-build -o yaml

# 3. EXPLORE INSTANCES - Filter by family pattern with constraints
runson family "r8*"
runson family "c6g*" --cpu=8 --mem=16
runson family "r8*" --cpu=4:16 --mem=16:64
runson family "c6g*" --arch=arm64 --budget=0.50

# 4. FIND TMPFS-SUITABLE INSTANCES - For RAM-based storage workloads
runson family --cpu=16 --for-tmpfs --pick-family --globs
runson family --cpu=32 --mem=64 --for-tmpfs --pick-family -o yaml

# 5. FILTER BY EBS BANDWIDTH - For I/O intensive workloads
runson family "r8*" --cpu=8 --ebs-min=5000
runson family --runner=grype-db-build --sort=ebs

# Find alternative families for a runner's CPU/RAM requirements
runson family --runner=grype-db-build --pick-family -o yaml

# 6. USE CUSTOM CONFIG FILE - Point to a different runs-on.yml
runson family --config=/path/to/runs-on.yml "r8*"
runson family -c other-repo/.github/runs-on.yml --runner=my-runner
```

### Options

| Option | Description |
|--------|-------------|
| `-c, --config` | Path to runs-on.yml config file (default: `.github/runs-on.yml`) |
| `--cpu` | vCPU filter: exact (`8`) or range (`8:16`) |
| `--mem` | RAM filter in GB: exact (`16`) or range (`16:32`) |
| `--runner` | Use a runner's config from runs-on.yml |
| `-o, --output` | Output format: `list` (table) or `yaml` |
| `--pick-family` | Ignore family restrictions, show all matching cpu/ram |
| `--globs` | Output minimal glob patterns that select the filtered instances |
| `--arch` | Filter by architecture (arm64, amd64, x86_64). Repeatable. |
| `--budget` | Max hourly price (on-demand) |
| `--ebs-min` | Minimum EBS bandwidth in Mbps |
| `--for-tmpfs` | Filter for tmpfs-suitable instances (32GB+ RAM) |
| `--sort` | Sort by: price, spot, vcpus, memory, api_name, arch, ebs |

### Output columns

| Column | Description |
|--------|-------------|
| API Name | Instance type (e.g., `r8g.2xlarge`) |
| Price | On-demand hourly cost |
| Spot | Spot instance hourly cost |
| Arch | CPU architecture (`arm64`, `amd64`, `x86_64`) |
| Memory | RAM in GB |
| vCPUs | Virtual CPU count |
| EBS Mbps | EBS baseline bandwidth in Mbps |
| Matched By | Runners from `runs-on.yml` that would match this instance |

### tmpfs Mode

The `--for-tmpfs` flag filters for instances suitable for [runs-on.com tmpfs](https://runs-on.com/caching/tmpfs/) - using RAM as filesystem storage. This is ideal for I/O intensive workloads where data doesn't need persistence (e.g., pulled from S3).

When enabled:
- Sets minimum RAM to 32GB (if no `--mem` specified)
- Shows instances from memory-optimized families (r-series)

Configure tmpfs in your runner with `extras: s3-cache+tmpfs`:

```yaml
runners:
  my-runner:
    cpu: 16
    ram: [128]
    extras: s3-cache+tmpfs
    family:
      - "r8g.4xlarge"
```

---

## runson estimate

Estimates the cost of a specific GitHub Actions workflow run by analyzing job durations and matching them to runner pricing.

### Usage

```bash
# Analyze workflow from another repo (auto-fetches its runs-on.yml config)
runson estimate https://github.com/anchore/grype/actions/runs/123

# Using just the run ID (uses current repo and local config)
runson estimate 20529466040

# Use explicit local config file
runson estimate 123 --config=/path/to/runs-on.yml

# Analyze remote workflow but use local config instead of fetching
runson estimate https://github.com/anchore/grype/actions/runs/123 --no-fetch-config
```

**Note:** When a full GitHub URL is provided, the command automatically fetches
`runs-on.yml` from the target repository. Use `--no-fetch-config` to use the
local repository's config instead.

### Options

| Option | Description |
|--------|-------------|
| `-c, --config` | Path to local runs-on.yml config file |
| `--no-fetch-config` | Don't auto-fetch config from target repo |

### How it works

1. Fetches workflow run and job data via `gh api`
2. For each job, extracts:
   - Duration from `started_at` and `completed_at` timestamps
   - Runner name from job labels (e.g., `runner=grype-db-pull`)
3. Looks up runner config in `.github/runs-on.yml` to get instance families
4. Finds the cheapest matching instance price from bundled AWS pricing data
5. Calculates cost: `duration_hours * hourly_rate`

### Output

Shows per-job breakdown with duration, runner, and cost:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Grype DBs: publish (#4254)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Status: success
  Duration: 45m 32s

Jobs:
  ✓ Read configuration              0m 34s  ubuntu-24.04        $0.005 (GitHub-hosted)
  ✓ Provider (ubuntu)              12m 04s  grype-db-pull       $0.094 (r8g.2xlarge @ $0.47/hr)
  ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TOTAL COST ESTIMATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  $0.92 (lower bound)
  $1.42 (upper bound)
```

### Supported runners

- **runs-on.com runners**: Uses bundled AWS pricing data based on runner config in `.github/runs-on.yml`
- **GitHub standard runners**: `ubuntu-24.04`, `windows-latest`, etc. ($0.008/min Linux, $0.016/min Windows, $0.08/min macOS)
- **GitHub larger runners**: Parses labels like `Linux_x64_8Core_32gbRam_300gbSSD` and applies correct per-minute pricing

#### GitHub larger runner pricing (per-minute)

| Cores | Linux x64 | Linux ARM64 | Windows x64 |
|-------|-----------|-------------|-------------|
| 2 | $0.008 | $0.005 | - |
| 4 | $0.016 | $0.010 | $0.032 |
| 8 | $0.032 | $0.020 | $0.064 |
| 16 | $0.064 | $0.040 | $0.128 |
| 32 | $0.128 | $0.080 | $0.256 |
| 64 | $0.256 | $0.160 | $0.512 |

Source: [GitHub Actions runner pricing](https://docs.github.com/en/billing/reference/actions-runner-pricing)

### Requirements

- `gh` CLI must be installed and authenticated
