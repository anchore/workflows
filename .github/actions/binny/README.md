# Binny

A GitHub Action that installs project tools managed by [binny](https://github.com/anchore/binny) with caching support.

## Why use this?

Projects often have multiple CLI tools as development dependencies (linters, formatters, etc.). Binny manages these tools declaratively via a `.binny.yaml` file. This action:

- Installs all tools defined in `.binny.yaml`
- Caches the `.tool` directory between runs for faster CI
- Verifies installed tools match the expected versions

## Usage

```yaml
- uses: ./.github/actions/binny
  with:
    cache-key-prefix: "abc123"
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `cache-key-prefix` | No | `181053ac82` | Prefix for cache keys (change to invalidate cache) |

## Outputs

None.

## Prerequisites

The repository must have:
- A `.binny.yaml` file defining the tools to install
- A `Makefile` with a `tools` target that runs binny

## Example Workflow

```yaml
name: CI

on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install tools
        uses: ./.github/actions/binny

      - name: Run linter
        run: .tool/golangci-lint run
```

## Cache Behavior

The action caches the `.tool` directory based on:
- The `cache-key-prefix` input
- The runner OS
- A hash of the `.binny.yaml` file

To invalidate the cache, change the `cache-key-prefix` value.
