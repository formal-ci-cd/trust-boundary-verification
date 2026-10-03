"""Summarize fixed, locally saved A1-A5 tool output at property level."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path


CASES = {
    "A1": ("artifact-a1-pr-producer.yml", "artifact-a1-unsafe-consumer.yml"),
    "A2": ("artifact-a1-pr-producer.yml", "artifact-a2-safe-consumer.yml"),
    "A3": ("artifact-a1-pr-producer.yml", "artifact-a3-download-only-consumer.yml"),
    "A4": ("artifact-a1-pr-producer.yml", "artifact-a4-no-authority-consumer.yml"),
    "A5": ("artifact-a5-attest-producer.yml", "artifact-a5-attest-consumer.yml"),
}


def sarif_findings(payload):
    return [
        {
            "rule": result.get("ruleId"),
            "file": location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri", ""),
        }
        for run in payload["runs"]
        for result in run.get("results", [])
        for location in result.get("locations", [])[:1]
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zizmor", type=Path, required=True)
    parser.add_argument("--sisakulint", type=Path, required=True)
    parser.add_argument("--poutine", type=Path, required=True)
    parser.add_argument("--codeql-default", type=Path, required=True)
    parser.add_argument("--codeql-broad", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sources = {"zizmor": args.zizmor, "sisakulint": args.sisakulint,
               "poutine": args.poutine, "codeql-default": args.codeql_default,
               "codeql-broad": args.codeql_broad}
    findings = {}
    provenance = {}
    for tool, path in sources.items():
        raw = path.read_bytes()
        payload = json.loads(raw)
        if tool == "poutine":
            findings[tool] = [
                {"rule": item.get("meta", {}).get("rule_id"), "file": item.get("meta", {}).get("path", "")}
                for item in payload["findings"]
            ]
        else:
            findings[tool] = sarif_findings(payload)
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        (args.output / f"{tool}.json.gz").write_bytes(compressed)
        provenance[tool] = {
            "output": f"{tool}.json.gz",
            "rawSHA256": hashlib.sha256(raw).hexdigest(),
            "compressedSHA256": hashlib.sha256(compressed).hexdigest(),
            "totalFindings": len(findings[tool]),
        }

    comparisons = {}
    for case, (producer, consumer) in CASES.items():
        comparisons[case] = {}
        for tool, all_findings in findings.items():
            matching = [f for f in all_findings if Path(f["file"]).name in (producer, consumer)]
            target = [f for f in matching if (tool == "zizmor" and f["rule"] == "zizmor/dangerous-triggers")]
            comparisons[case][tool] = {
                "allFindingsAtEndpoints": matching,
                "targetRelatedFindings": target,
                "propertyLevelClass": "入口のみ" if target else "対象findingなし",
                "classificationDetail": "consumer側workflow_run triggerの局所警告" if target else "対象propertyのfindingなし",
                "sameFindingLinksProducerSharedObjectConsumerAuthority": 0,
            }

    report = {
        "scope": "A1-A5 artifact property; fixed local outputs, not a general capability claim",
        "versions": {"zizmor": "1.30.1", "sisakulint": "0.3.7", "poutine": "1.1.6",
                     "codeql": "2.27.1", "codeqlQueryPack": "codeql/actions-queries@0.6.36"},
        "codeqlMacOSArchiveSHA256": "412c600764a7835f9548af120d0bdadea1040c6f68b8f6bf04ec72a664891f63",
        "inputs": "CodeQL, zizmor and sisakulint: seven artifact-a*.yml workflows; Poutine: entire local research repository",
        "inputSHA256": {name: hashlib.sha256((args.root / '.github/workflows' / name).read_bytes()).hexdigest()
                        for name in sorted({p for pair in CASES.values() for p in pair})},
        "commands": {
            "zizmor": "zizmor --offline --format sarif <seven artifact workflows>",
            "sisakulint": "sisakulint -fix off -format '{{sarif .}}' <seven artifact workflows>",
            "poutine": "poutine analyze_local <research repository> --disable-version-check --format json",
            "codeql-default": "codeql database create --language=actions <seven-workflow source>; codeql database analyze codeql/actions-queries@0.6.36:codeql-suites/actions-code-scanning.qls",
            "codeql-broad": "codeql database analyze same database codeql/actions-queries@0.6.36:codeql-suites/actions-security-and-quality.qls",
        },
        "limitations": [
            "This is a reviewed rule-and-location classification of saved output, not a proof of tool incapability.",
            "zizmor dangerous-triggers warns at workflow_run consumers but does not link a producer or artifact in one result.",
            "zizmor template-injection warnings in A5 concern a different property.",
            "sisakulint reported no GitHub token; online API checks may be limited.",
            "Poutine was scanned with full repository context, while the other tools received only seven workflow files.",
            "CodeQL's zero results describe these two fixed suites and inputs only, not all possible custom queries.",
        ],
        "outputs": provenance,
        "cases": comparisons,
    }
    (args.output / "property-level-comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
