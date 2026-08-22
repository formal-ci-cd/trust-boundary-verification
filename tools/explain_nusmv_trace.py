#!/usr/bin/env python3
"""NuSMVの反例を元のworkflow，job，stepへ対応付ける．"""

import argparse
import json
import re
from pathlib import Path


STAGE_PATTERN = re.compile(r"^\s*stage = ([a-z_]+)\s*$")


def parse_stages(output):
    stages = []
    for line in output.splitlines():
        match = STAGE_PATTERN.match(line)
        if match and (not stages or stages[-1] != match.group(1)):
            stages.append(match.group(1))
    return stages


def render_explanation(chain, nusmv_output):
    stages = parse_stages(nusmv_output)
    violated = "is false" in nusmv_output
    lines = [
        f"# {chain['scenario']['id']}のNuSMV検証経路",
        "",
        f"安全性：{'違反する反例あり' if violated else '違反する反例なし'}",
        "",
    ]
    if not stages:
        lines.append("NuSMV出力に状態経路は含まれていない．")
        lines.append("")
        return "\n".join(lines)

    lines.extend(
        [
            "| 順序 | 状態 | workflow | job | step | 意味 |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for index, stage in enumerate(stages, start=1):
        location = chain["traceMap"].get(stage)
        if location is None:
            continue
        lines.append(
            "| {index} | `{stage}` | `{workflow}` | `{job}` | {step} | {meaning} |".format(
                index=index,
                stage=stage,
                workflow=location["workflowFile"],
                job=location["jobId"],
                step=location["stepIndex"],
                meaning=location["meaning"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("chain", type=Path)
    parser.add_argument("nusmv_output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.chain.open(encoding="utf-8") as source:
        chain = json.load(source)
    output = render_explanation(
        chain, args.nusmv_output.read_text(encoding="utf-8")
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
