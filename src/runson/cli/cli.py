"""Main CLI group and logging setup for runson."""

from __future__ import annotations

import logging

import click
import colorlog


def setup_logging(verbosity: int) -> None:
    """Configure logging based on verbosity level."""
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s %(message)s",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


@click.group()
@click.version_option(package_name="runson")
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v for INFO, -vv for DEBUG)")
@click.pass_context
def cli(ctx: click.Context, verbose: int) -> None:
    """Tools for analyzing runs-on.com configuration and costs."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# import and register subcommands
from . import estimate as estimate_module  # noqa: E402
from . import family as family_module  # noqa: E402

cli.add_command(family_module.family)
cli.add_command(estimate_module.estimate)
