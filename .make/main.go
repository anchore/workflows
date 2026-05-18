package main

import (
	. "github.com/anchore/go-make"
	"github.com/anchore/go-make/run"
	"github.com/anchore/go-make/tasks/release"
)

const (
	runsonSrcDir          = "src/runson"
	runsonTestDir         = "tests"
	waitForCheckActionDir = ".github/actions/wait-for-check"
)

func main() {
	Makefile(
		// shared anchore tasks: changelog, release (workflow trigger), ci-release (tag + GH release)
		release.Tasks(),

		// default validation pipeline (this is what `make` runs with no target)
		Task{
			Name:         "default",
			Description:  "Run all validation tasks",
			Dependencies: Deps("static-analysis"),
		},
		Task{
			Name:         "static-analysis",
			Description:  "Run all static analysis tasks",
			Dependencies: Deps("lint", "runson:static-analysis", "wait-for-check:static-analysis"),
		},

		// repo-wide workflow lint (wrkflw is binny-managed; go-make resolves it via .tool/)
		Task{
			Name:        "lint",
			Description: "Lint github workflows",
			Run:         func() { Run("wrkflw validate") },
		},

		runsonTasks(),
		waitForCheckTasks(),
	)
}

func runsonTasks() Task {
	return Task{
		Tasks: []Task{
			{
				Name:         "runson:static-analysis",
				Description:  "Run all runson static analysis tasks",
				Dependencies: Deps("runson:lint", "runson:format-check", "runson:check-types"),
			},
			{
				Name:        "runson:lint",
				Description: "Run ruff linter on runson code",
				Run:         func() { Run("uv run ruff check .") },
			},
			{
				Name:        "runson:lint-fix",
				Description: "Run ruff linter with auto-fix on runson code",
				Run:         func() { Run("uv run ruff check . --fix") },
			},
			{
				Name:        "runson:format",
				Description: "Format runson code with ruff",
				Run:         func() { Run("uv run ruff format .") },
			},
			{
				Name:        "runson:format-check",
				Description: "Check runson code formatting",
				Run:         func() { Run("uv run ruff format --check .") },
			},
			{
				Name:        "runson:check-types",
				Description: "Run mypy type checker on runson code",
				Run:         func() { Run("uv run mypy --config-file ./pyproject.toml " + runsonSrcDir) },
			},
			{
				Name:        "runson:test",
				Description: "Run runson tests with pytest",
				Run:         func() { Run("uv run pytest " + runsonTestDir + " -v") },
			},
			{
				Name:        "runson:test-cov",
				Description: "Run runson tests with coverage",
				Run: func() {
					Run("uv run pytest " + runsonTestDir + " -v --cov=" + runsonSrcDir + " --cov-report=html")
				},
			},
		},
	}
}

func waitForCheckTasks() Task {
	// wait-for-check has its own pyproject.toml inside the action directory; tooling
	// runs from there so ruff/pytest pick up the right config.
	inDir := func(cmd string) { Run(cmd, run.InDir(waitForCheckActionDir)) }
	ruffCmd := func(args string) func() {
		return func() {
			inDir("pip install ruff -q")
			inDir("ruff " + args)
		}
	}
	return Task{
		Tasks: []Task{
			{
				Name:         "wait-for-check:static-analysis",
				Description:  "Run all wait-for-check static analysis tasks",
				Dependencies: Deps("wait-for-check:lint", "wait-for-check:format-check"),
			},
			{
				Name:        "wait-for-check:lint",
				Description: "Run ruff linter on wait-for-check code",
				Run:         ruffCmd("check ."),
			},
			{
				Name:        "wait-for-check:lint-fix",
				Description: "Run ruff linter with auto-fix on wait-for-check code",
				Run:         ruffCmd("check . --fix"),
			},
			{
				Name:        "wait-for-check:format",
				Description: "Format wait-for-check code with ruff",
				Run:         ruffCmd("format ."),
			},
			{
				Name:        "wait-for-check:format-check",
				Description: "Check wait-for-check code formatting",
				Run:         ruffCmd("format --check ."),
			},
			{
				Name:        "wait-for-check:test",
				Description: "Run wait-for-check tests with pytest",
				Run: func() {
					inDir("pip install pytest -q")
					inDir("pytest -v")
				},
			},
		},
	}
}
