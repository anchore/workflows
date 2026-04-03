# Python

A GitHub Action that sets up a Python environment using [uv](https://github.com/astral-sh/uv) for fast, reliable dependency management.

## Why use this?

Setting up Python in CI typically requires multiple steps: installing Python, installing a package manager, and installing dependencies. This action combines everything into one step using uv, which is significantly faster than pip.

## Usage

```yaml
- uses: ./.github/actions/python
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `python-version` | No | `3.13` | Python version to install |
| `uv-version` | No | `0.7.x` | Version of uv to install |
| `cache-key-prefix` | No | `181053ac82` | Prefix for cache keys (change to invalidate cache) |

## Outputs

None.

## Prerequisites

The repository should have a `pyproject.toml` file with dependencies defined. The action runs `uv sync --extra dev` to install all dependencies including dev extras.

## Example Workflow

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: ./.github/actions/python
        with:
          python-version: "3.12"

      - name: Run tests
        run: pytest

      - name: Run linter
        run: ruff check .
```

## What It Does

1. Installs uv (with caching enabled)
2. Installs the specified Python version
3. Runs `uv sync --extra dev` to install project dependencies

## Why uv?

[uv](https://github.com/astral-sh/uv) is an extremely fast Python package installer written in Rust. It's typically 10-100x faster than pip, which significantly speeds up CI runs.
