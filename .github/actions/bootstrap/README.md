# Bootstrap

A GitHub Action that sets up a complete development environment by combining the `binny` and `python` actions.

## Why use this?

For projects that need both binny-managed tools and a Python environment, this action provides a single step to set up everything. It's a convenience wrapper that:

- Installs all binny-managed tools (with caching)
- Sets up Python with uv and project dependencies

## Usage

```yaml
- uses: ./.github/actions/bootstrap
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `python-version` | No | `3.13` | Python version to install |
| `cache-key-prefix` | No | `181053ac82` | Prefix for cache keys (change to invalidate cache) |

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
        with:
          python-version: "3.12"

      - name: Run tests
        run: pytest
```

## What It Does

This action runs two sub-actions in sequence:

1. **binny** - Installs CLI tools from `.binny.yaml`
2. **python** - Sets up Python with uv and installs dependencies from `pyproject.toml`

If you only need one of these, use the individual actions instead.
