"""Command-line entrypoint.

Runs the same pipeline as the API and emits the identical :class:`TransformResponse`
JSON, so ``python -m app.cli`` and ``POST /transform`` are interchangeable for the
same inputs::

    python -m app.cli --inputs a.json b.txt --config config.json --out result.json

With no ``--config`` (or ``--config none``), the full default canonical projection
is produced.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.api.schemas import TransformResponse, build_transform_response
from app.domain.models import Config
from app.pipeline.orchestrate import run


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="candidate-transformer",
        description="Merge multi-source candidate data into one canonical profile.",
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        metavar="PATH",
        help="One or more source files (ATS JSON, resume TXT/PDF/DOCX).",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH|none",
        help="Projection config JSON file; omit or 'none' for the full schema.",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write the JSON result here; defaults to stdout.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="candidate-transformer 0.1.0",
    )
    return parser


def load_config(value: str | None) -> Config:
    """Load a projection config from a path; ``None``/``"none"`` is the default."""
    if value is None or value.strip().lower() == "none":
        return Config()
    parsed = json.loads(Path(value).read_text(encoding="utf-8"))
    return Config.model_validate(parsed)


def transform_inputs(inputs: list[str], config_arg: str | None) -> TransformResponse:
    """Run the pipeline over input paths and build the public response shape."""
    config = load_config(config_arg)
    paths = [Path(item) for item in inputs]
    return build_transform_response(run(paths, config))


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    inputs: list[str] = list(args.inputs)
    config_arg: str | None = args.config
    out_arg: str | None = args.out

    response = transform_inputs(inputs, config_arg)
    output = response.model_dump_json(indent=2)

    if out_arg is not None:
        Path(out_arg).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
