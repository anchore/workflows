# Create Tag via Deploy Key

A GitHub Action that creates and pushes git tags using an SSH deploy key for authentication, instead of the standard `GITHUB_TOKEN`.

## Why use this?

The default `GITHUB_TOKEN` has broad permissions that can be difficult to restrict. By using a deploy key with repository rulesets, you can:

- Allow tag pushes while blocking direct code pushes
- Maintain fine-grained control over what CI can modify
- Comply with branch protection and tag protection rules

## Usage

```yaml
- uses: ./.github/actions/create-tag-via-deploy-key
  with:
    tag: v1.2.3
    deploy-key: ${{ secrets.DEPLOY_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `tag` | Yes | - | The tag to create (e.g., `v1.2.3`) |
| `deploy-key` | Yes | - | SSH private key with write access to the repository |
| `tag-message` | No | `Release <tag>` | Message for the annotated tag |
| `git-user-name` | No | `github-actions[bot]` | Git user.name for the tag commit |
| `git-user-email` | No | `github-actions[bot]@users.noreply.github.com` | Git user.email for the tag commit |

## Outputs

| Output | Description |
|--------|-------------|
| `tag` | The tag that was created |
| `sha` | The commit SHA that was tagged |

## Setting up a Deploy Key

1. Generate an SSH key pair:
   ```bash
   ssh-keygen -t ed25519 -C "deploy-key" -f deploy_key -N ""
   ```

2. Add the **public key** (`deploy_key.pub`) as a deploy key in your repository:
   - Go to Settings > Deploy keys > Add deploy key
   - Check "Allow write access"

3. Add the **private key** (`deploy_key`) as a repository secret:
   - Go to Settings > Secrets and variables > Actions > New repository secret
   - Name it `DEPLOY_KEY` (or your preferred name)
   - Paste the entire private key content

4. (Optional) Configure repository rulesets to restrict what the deploy key can do.

## Example Workflow

```yaml
name: Release

on:
  push:
    branches: [main]

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Determine version
        id: version
        run: echo "version=v1.0.0" >> $GITHUB_OUTPUT

      - name: Create tag
        uses: ./.github/actions/create-tag-via-deploy-key
        with:
          tag: ${{ steps.version.outputs.version }}
          deploy-key: ${{ secrets.DEPLOY_KEY }}
          tag-message: "Release ${{ steps.version.outputs.version }}"
```

## Limitations

- Only works with GitHub repositories (uses `git@github.com` SSH URL)
- Requires a deploy key with write access
- Tag must not already exist locally or remotely
