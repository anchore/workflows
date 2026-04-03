# Wait for Check

A GitHub Action that polls for a GitHub check run to complete and returns its conclusion. Useful for coordinating workflows that depend on other checks finishing first.

## Why use this?

GitHub Actions workflows sometimes need to wait for other checks to complete before proceeding. For example:
- A release workflow that should only run after all CI checks pass
- A deployment that depends on security scans completing
- Coordinating between parallel jobs that can't use `needs`

## Usage

```yaml
- uses: ./.github/actions/wait-for-check
  with:
    token: ${{ github.token }}
    check-name: "Build"
    ref: ${{ github.sha }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `token` | Yes | - | GitHub token for API calls |
| `check-name` | Yes | - | Name of the check to wait for |
| `ref` | Yes | - | Git ref (commit SHA) to check |
| `timeout-seconds` | No | `600` | Maximum time to wait before timing out |
| `interval-seconds` | No | `30` | How often to poll the GitHub API |

## Outputs

| Output | Description |
|--------|-------------|
| `conclusion` | The check conclusion: `success`, `failure`, `cancelled`, `skipped`, `timed_out`, etc. |

## Example Workflow

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  wait-for-ci:
    runs-on: ubuntu-latest
    outputs:
      ci-passed: ${{ steps.wait.outputs.conclusion == 'success' }}
    steps:
      - uses: actions/checkout@v4

      - name: Wait for CI
        id: wait
        uses: ./.github/actions/wait-for-check
        with:
          token: ${{ github.token }}
          check-name: "CI / Build"
          ref: ${{ github.sha }}
          timeout-seconds: 300
          interval-seconds: 15

      - name: Check result
        if: steps.wait.outputs.conclusion != 'success'
        run: |
          echo "CI check did not succeed: ${{ steps.wait.outputs.conclusion }}"
          exit 1

  release:
    needs: wait-for-ci
    if: needs.wait-for-ci.outputs.ci-passed == 'true'
    runs-on: ubuntu-latest
    steps:
      - run: echo "Releasing..."
```

## Behavior

- Polls the GitHub API at the specified interval until the check completes or times out
- Returns `timed_out` as the conclusion if the timeout is reached
- Handles API errors gracefully with retries
- The check must match by exact name
