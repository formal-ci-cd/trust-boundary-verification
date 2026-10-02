#!/usr/bin/env python3
"""Compare the scope of saved TanStack SARIF findings with the model path.

This counts files actually attached to each SARIF result, including code-flow
and related locations. It does not infer a tool's theoretical capability or
equate a local entry warning with a complete cross-workflow attack path.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


INPUTS = {
    "codeql-incident-available-default": {
        "pre-incident": "results/tanstack-cache-chain/historical-codeql-2026-05/pre-incident-default.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/historical-codeql-2026-05/mitigation-default.sarif.gz",
    },
    "codeql-current-security-and-quality": {
        "pre-incident": "results/tanstack-cache-chain/pre-incident/codeql-security-and-quality.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/mitigation/codeql-security-and-quality.sarif.gz",
    },
    "zizmor-current-regular": {
        "pre-incident": "results/tanstack-cache-chain/pre-incident/zizmor-regular.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/mitigation/zizmor-regular.sarif.gz",
    },
    "sisakulint-current": {
        "pre-incident": "results/additional-sisakulint-baseline/tanstack-pre-incident.sarif.gz",
        "mitigation": "results/additional-sisakulint-baseline/tanstack-mitigation.sarif.gz",
    },
}
PRODUCER = "bundle-size.yml"
CONSUMER = "release.yml"


def location_files(result):
    """Read every SARIF physicalLocation, including nested thread flows."""
    found = set()

    def visit(value):
        if isinstance(value, dict):
            physical = value.get("physicalLocation")
            if isinstance(physical, dict):
                uri = physical.get("artifactLocation", {}).get("uri")
                if uri:
                    found.add(Path(uri).name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return sorted(found)


def summarize_sarif(path):
    compressed = path.read_bytes()
    sarif = json.loads(gzip.decompress(compressed))
    results = [result for run in sarif["runs"] for result in run.get("results", [])]
    entries = []
    for result in results:
        files = location_files(result)
        entries.append(
            {
                "rule": result["ruleId"],
                "files": files,
                "mentionsConsumerInMessage": CONSUMER in result.get("message", {}).get("text", ""),
            }
        )
    return {
        "inputSHA256": hashlib.sha256(compressed).hexdigest(),
        "findingCount": len(entries),
        "producerFindingCount": sum(PRODUCER in item["files"] for item in entries),
        "consumerFindingCount": sum(CONSUMER in item["files"] for item in entries),
        "sameFindingLinksProducerAndConsumer": sum(
            PRODUCER in item["files"] and CONSUMER in item["files"]
            for item in entries
        ),
        "producerFindingMentionsConsumer": sum(
            PRODUCER in item["files"] and item["mentionsConsumerInMessage"]
            for item in entries
        ),
        "findingsAtProducerOrConsumer": [
            item for item in entries if PRODUCER in item["files"] or CONSUMER in item["files"]
        ],
    }


def compare(root):
    analysis_path = root / "results/tanstack-cache-chain/analysis.json"
    analysis = json.loads(analysis_path.read_text())
    cases = {case["variant"]: case for case in analysis["cases"]}
    model = {}
    for variant in ("pre-incident", "mitigation"):
        case = cases[variant]
        evidence = sorted({Path(item["file"]).name for item in case["evidence"]})
        if not {PRODUCER, CONSUMER}.issubset(evidence):
            raise ValueError(f"Model evidence lacks endpoints: {variant}")
        if case["bfs"]["verdict"] != case["nusmv"]["verdict"]:
            raise ValueError(f"Independent BFS/NuSMV disagree: {variant}")
        model[variant] = {
            "staticVerdict": case["staticVerdict"],
            "formalVerdict": case["nusmv"]["verdict"],
            "evidenceFiles": evidence,
            "conditionalCounterexampleSteps": [
                item["step"] for item in case["bfs"]["conditionalCounterexample"]
            ],
        }
    tools = {}
    for name, paths in INPUTS.items():
        tools[name] = {}
        for variant, relative in paths.items():
            tools[name][variant] = {"input": relative, **summarize_sarif(root / relative)}
    return {
        "property": analysis["property"],
        "scope": "Fixed TanStack incident and mitigation snapshots; only the listed SARIF runs and saved model output",
        "interpretation": "A zero same-finding count describes these emitted SARIF results, not inability to implement a custom query or absence of local warnings. Model counterexample is conditional on runtime facts; incident observations are separate.",
        "producerFile": PRODUCER,
        "consumerFile": CONSUMER,
        "model": model,
        "tools": tools,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.root), indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
