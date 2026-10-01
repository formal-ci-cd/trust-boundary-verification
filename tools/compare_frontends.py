#!/usr/bin/env python3
"""同じYAMLに対するCodeQL抽出とYAML直接解析の構造/完全runを比較する．"""
import argparse
import json
from pathlib import Path
import discover_artifact_chains
import yaml_to_model


def canonical(model):
    workflow = model["workflow"]
    return {
        "file": workflow["file"],
        "name": workflow["name"],
        "events": sorted(
            [
                {
                    "name": e["name"],
                    "properties": {
                        k: sorted(v) for k, v in e.get("properties", {}).items()
                    },
                }
                for e in workflow["events"]
            ],
            key=lambda e: e["name"],
        ),
        "permissions": sorted(
            workflow["permissions"],
            key=lambda p: (p["jobId"] or "", p["scope"], p["access"]),
        ),
        "jobs": sorted(
            [
                {"id": j["id"], "runnerLabels": sorted(j["runnerLabels"])}
                for j in workflow["jobs"]
            ],
            key=lambda j: j["id"],
        ),
        "sharedStateOperations": sorted(
            model["sharedStateOperations"], key=lambda o: o["id"]
        ),
        "runScripts": {
            j["id"] + ":" + str(s["index"]): s["command"]
            for j in workflow["jobs"]
            for s in j["steps"]
            if s["type"] == "run" and s.get("commandOrderKnown")
        },
    }


def compare(root, csv):
    yaml = {
        m["workflow"]["file"]: canonical(m)
        for m in yaml_to_model.load_models(root).values()
    }
    codeql = {
        m["workflow"]["file"]: canonical(m)
        for m in discover_artifact_chains.load_models(csv).values()
    }
    differences = []
    for file in sorted(set(yaml) | set(codeql)):
        if file not in yaml or file not in codeql:
            differences.append({"file": file, "field": "missingWorkflow"})
            continue
        for field in yaml[file]:
            if yaml[file][field] != codeql[file][field]:
                differences.append(
                    {
                        "file": file,
                        "field": field,
                        "yaml": yaml[file][field],
                        "codeql": codeql[file][field],
                    }
                )
    return {
        "workflowCount": len(yaml),
        "comparedFields": [
            "file",
            "name",
            "events",
            "permissions",
            "jobs",
            "sharedStateOperations",
            "runScripts",
        ],
        "semanticsCompared": False,
        "equivalent": not differences,
        "differences": differences,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("csv", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = compare(args.root, args.csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if not result["equivalent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
