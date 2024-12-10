import subprocess
import re
import os
import sys


def main(repos_input: list[str], allow_downgrade: bool = False):
    changelog_entries = []

    draft = "false"
    has_downgrade = False
    has_change = False
    for repo_info in repos_input:
        repo, version = repo_info.strip().split('@')
        print(f"Updating {repo} to {version}")

        # get original version (fails if not in go.mod file)
        original_version = run(f"go list -m -f '{{{{.Version}}}}' {repo}", capture_output=True).stdout.strip()
        if not original_version:
            raise RuntimeError(f"Dependency {repo} not found in go.mod file")

        # perform the `go get` command to update the dependency
        log = run(f"go get {repo}@{version}", shell=True, text=True, capture_output=True).stderr.strip()

        # check for downgrade or update
        if f"downgraded {repo}" in log:
            action = "downgrade"
            # we want this script to always dictate the draft status relative to the https://github.com/peter-evans/create-pull-request
            # github action. In this case always-true will update the value on PR creation and updates.
            draft = "always-true"
            has_downgrade = True
        else:
            action = "update"

        # get the resolved version after go get
        resolved_version = run(f"go list -m -f '{{{{.Version}}}}' {repo}", capture_output=True).stdout.strip()

        if resolved_version == "unknown" or "-" in resolved_version:
            draft = "always-true"

        # tidy up the go.mod file
        run("go mod tidy", capture_output=False)

        # create the changelog entry
        repo_name = repo.split('/')[-1].capitalize()

        if original_version == resolved_version:
            changelog_entry = f" - **{repo_name}**: not changed (requested `{version}`)"
        else:
            has_change = True
            changelog_entry = f" - **{repo_name}**: `{original_version}` ➔ `{resolved_version}`"

            if resolved_version != version:
                changelog_entry += f" (requested `{version}`)"

            if action == "downgrade":
                changelog_entry += " 🔴 ***Downgrade***"
        changelog_entries.append(changelog_entry)

    if not has_change:
        print("No dependencies were changed")
        sys.exit(1)

    # construct the full changelog body
    pr_body = ""
    if has_downgrade:
        pr_body = "> [!WARNING]\n> Some dependencies were downgraded, please review if this was intentional\n\n"
    pr_body += "## Dependencies changed\n"
    pr_body += "\n".join(changelog_entries)

    print(pr_body)

    if not allow_downgrade and has_downgrade:
        print("Downgrades are not allowed")
        sys.exit(1)

    print()
    print(f"Draft: {draft=='always-true'}")

    # write the changelog output
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        print("No output file provided via $GITHUB_OUTPUT")
    else:
        with open(output_file, "a") as output_file:
            # why the heredoc approach? This is how multiline strings are added as output variables
            # see https://github.com/github/docs/issues/21529
            output_file.write("summary<<EOF\n")
            output_file.write(f"{pr_body}\n")
            output_file.write("EOF\n")
            output_file.write(f"draft={draft}\n")

    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_file:
        print("No summary file provided via $GITHUB_STEP_SUMMARY")
    else:
        # though the calling PR might create a PR with the changes we just made, lets at least add the same PR
        # body to the actions summary for easy reading
        with open(summary_file, "a") as output_file:
            output_file.write(f"{pr_body}\n")


# run a shell command and print the output
def run(cmd, **kwargs):
    opts = {
        "shell": True,
        "text": True,
        "check": True,
    }
    opts.update(kwargs)

    print(cmd.strip())
    if "capture_output" not in opts or not opts["capture_output"]:
        opts.update({
            "stdout": None,
            "stderr": None,
        })
        return subprocess.run(cmd, **opts)

    opts["capture_output"] = True

    result = subprocess.run(cmd, **opts)

    if result.stdout:
        out = result.stdout.strip()
        if out:
            print(out)
    if result.stderr:
        out = result.stderr.strip()
        if out:
            print(out)
    print()

    return result


if __name__ == "__main__":
    repos_str = os.environ.get("INPUT_REPOS")
    if not repos_str:
        print("No repos provided via $INPUT_REPOS")
        exit(1)

    repos_input = [x.strip() for x in re.split("[\n|,]", repos_str.strip())]

    if not os.environ.get("CI"):
        raise RuntimeError("This script is meant to be run in a CI environment")

    main(repos_input)
