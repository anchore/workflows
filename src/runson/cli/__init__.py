"""CLI entry point for runson."""

from . import cli as cli_module


def run() -> None:
    """Entry point for the runson CLI."""
    cli_module.cli()
