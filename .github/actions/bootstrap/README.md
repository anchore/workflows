# Bootstrap

A GitHub Action that sets up a complete development environment for this repo: Go + go-make, binny-managed CLI tools, and a Python environment via uv.

## Why use this?

This action is a thin wrapper around [`anchore/go-make/.github/actions/setup`](https://github.com/anchore/go-make/blob/main/.github/actions/setup/action.yaml) that adds the Python setup steps this repo also needs. It provides a single step to:

- Set up Go (with restore-only build/mod cache)
- Restore the binny tool cache and install binny-managed tools (`make binny:install`)
- Set up uv + Python and install project dependencies (`uv sync --extra dev`)

All cache writes are gated to pushes on the upstream repo's default branch only, so PRs and fork pushes are restore-only.

## Usage

```yaml
- uses: ./.github/actions/bootstrap
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `go-version` | No | `1.26.2` | Go version to install |
| `python-version` | No | `3.13` | Python version to install |
| `uv-version` | No | `0.7.x` | uv version to install |
| `cache-key-prefix` | No | `181053ac82` | Prefix for cache keys (change to invalidate cache) |
| `cache-enabled` | No | `true` | Enable build/mod and tool caching |

## Outputs

None.

## Example Workflow

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up environment
        uses: ./.github/actions/bootstrap

      - name: Run static analysis
        run: make static-analysis
```
