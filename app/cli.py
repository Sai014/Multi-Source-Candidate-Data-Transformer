"""Thin command-line entrypoint.

This is a placeholder for Step 0. Real wiring (point at input files + a config,
print/write the canonical JSON) is added in a later step once the pipeline exists.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="candidate-transformer",
        description="Merge multi-source candidate data into one canonical profile.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="candidate-transformer 0.1.0",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code.

    Step 0 stub: parses arguments and reports that the pipeline is not wired yet.
    """
    parser = build_parser()
    parser.parse_args(argv)
    print("candidate-transformer: scaffold ready; pipeline not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
