#!/usr/bin/env python3
"""Generate the stateful v2 SMV model for one producer/consumer pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import workflow_to_smv as base  # noqa: E402
from workflow_model_v2.builder import compile_pair_v2  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("producer", type=Path)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path)
    parser.add_argument("--scenario", type=Path,
                        help="use the extended bounded backend with explicit external run contexts")
    parser.add_argument("--workflow", type=Path, action="append", default=[],
                        help="additional bounded run workflow; requires --scenario")
    args = parser.parse_args()
    map_path = args.map_path or Path(str(args.output) + ".map.json")
    for target in (args.output, map_path):
        if target.exists() or target.is_symlink():
            parser.error(f"output already exists; use a new output path: {target}")
    try:
        if args.scenario:
            from workflow_model_v2.extended import compile_extended
            smv, manifest = compile_extended([args.producer, args.consumer, *args.workflow],
                                             json.loads(args.scenario.read_text()))
        else:
            if args.workflow:
                parser.error("--workflow requires --scenario")
            smv, manifest = compile_pair_v2(args.producer, args.consumer)
    except base.WorkflowError as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(smv, encoding="utf-8")
    map_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
