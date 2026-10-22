#!/usr/bin/env python3
"""Compile bounded workflow runs into SMV using one state-transition backend."""
import argparse
import json
from pathlib import Path
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.frontend import WorkflowError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('producer',type=Path)
    parser.add_argument('consumer',type=Path)
    parser.add_argument('--workflow',type=Path,action='append',default=[],help='additional bounded workflow run')
    parser.add_argument('--scenario',type=Path,help='explicit finite contexts; otherwise use inferred default contexts')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--map',dest='map_path',type=Path)
    args=parser.parse_args()
    mapping=args.map_path or Path(str(args.output)+'.map.json')
    for target in (args.output,mapping):
        if target.exists() or target.is_symlink():
            parser.error(f'output already exists; use a new output path: {target}')
    try:
        scenario=json.loads(args.scenario.read_text()) if args.scenario else None
        smv,manifest=compile_workflows([args.producer,args.consumer,*args.workflow],scenario)
    except (WorkflowError,OSError,ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    mapping.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(smv)
    mapping.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
