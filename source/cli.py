"""Command line: `simkit validate` and `simkit generate`."""

import argparse
import sys

from .adapters import ADAPTERS, get_adapter
from .dataset import DatasetWriter
from .scenario import Scenario


def generate(scenario, engine_name, output_dir, frame_count=None, start=0):
    """Render frames ``start`` .. ``start + frame_count - 1`` and write them as a dataset.

    ``frame_count`` defaults to the scenario's ``frames``.
    """
    n = frame_count if frame_count is not None else scenario.frames
    with get_adapter(engine_name)(scenario) as adapter, DatasetWriter(output_dir, scenario, engine_name) as writer:
        for index in range(start, start + n):
            frame = scenario.sample(index)
            adapter.apply(frame)
            writer.write(frame, adapter.capture())


def main(argv=None):
    """Entry point for the `simkit` command. ``argv`` defaults to the real command line."""
    parser = argparse.ArgumentParser(prog="simkit")
    subparser = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparser.add_parser("validate", help="check a scenario file and preview frame 0")
    validate_parser.add_argument("scenario")

    generate_parser = subparser.add_parser("generate", help="render a dataset with an engine adapter")
    generate_parser.add_argument("scenario")
    generate_parser.add_argument("--engine", default="mock", choices=sorted(ADAPTERS))
    generate_parser.add_argument("--out", required=True)
    generate_parser.add_argument("--frames", type=int, help="override the scenario's frame count")
    generate_parser.add_argument("--start", type=int, default=0, help="first frame index (for sharding)")

    args = parser.parse_args(argv)
    scenario = Scenario.from_yaml(args.scenario)

    if args.command == "validate":
        first_frame = scenario.sample(0)
        print(f"{scenario.name}: OK, {scenario.frames} frames, labels {scenario.labels()}")
        print(f"frame 0: {len(first_frame.objects)} objects, {len(first_frame.lights)} lights")
        return 0

    generate(scenario, args.engine, args.out, args.frames, args.start)
    print(f"wrote {args.frames or scenario.frames} frames to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
