#!/usr/bin/env python3
"""原本確認→公式CodeQL比較→YAML自動モデルを一括実行する．workflowは実行しない．"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import automatic_artifact_analysis as automatic
import chain_to_nusmv
import compare_frontends
import discover_artifact_chains
import yaml_to_model

CLI_VERSION = "2.27.1"
QUERY_PACK = "codeql/actions-queries@0.6.36"
SUITES = {
    "default": "actions-code-scanning.qls",
    "security-and-quality": "actions-security-and-quality.qls",
}


def verify_manifest(case):
    manifest = json.loads((case / "manifest.json").read_text())
    records = (manifest["files"] + manifest.get("externalAction", {}).get("files", [])
               + manifest.get("externalActionFixed", {}).get("files", []))
    for record in records:
        if (
            hashlib.sha256((case / record["snapshot"]).read_bytes()).hexdigest()
            != record["sha256"]
        ):
            raise ValueError("Source checksum mismatch: " + record["snapshot"])
    return manifest


def sarif_summary(path):
    sarif = json.loads(path.read_text())
    run = sarif["runs"][0]
    results = run.get("results", [])
    return {
        "alerts": len(results),
        "rules": dict(sorted(Counter(r["ruleId"] for r in results).items())),
        "locations": [
            {
                "rule": r["ruleId"],
                "location": r.get("locations", [{}])[0].get("physicalLocation", {}),
            }
            for r in results
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--codeql", type=Path, required=True)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--ram", type=int, default=6000)
    p.add_argument("--case", help="Evaluate only this public-case directory")
    args = p.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cli = str(args.codeql.resolve())
    version = json.loads(
        subprocess.check_output([cli, "version", "--format=json"], text=True)
    )
    if version["version"] != CLI_VERSION:
        raise ValueError(f'Expected CodeQL {CLI_VERSION}; got {version["version"]}')
    # lockにある構造抽出libraryと固定公式query packを使用する．
    subprocess.run([cli, "pack", "install", str(root / "codeql")], check=True)
    subprocess.run([cli, "pack", "download", QUERY_PACK], check=True)
    summary = {"codeql": version, "queryPack": QUERY_PACK, "cases": []}
    for case in sorted((root / "experiments/public-cases").iterdir()):
        if args.case and case.name != args.case:
            continue
        if not (case / "manifest.json").exists():
            continue
        manifest = verify_manifest(case)
        for variant in sorted({f["variant"] for f in manifest["files"]}):
            source = case / variant
            dest = output / (case.name + "-" + variant)
            dest.mkdir(exist_ok=True)
            db = dest / "database"
            subprocess.run(
                [
                    cli,
                    "database",
                    "create",
                    str(db),
                    "--language=actions",
                    "--source-root=" + str(source),
                ],
                check=True,
            )
            results = {}
            for suite, file in SUITES.items():
                sarif = dest / (suite + ".sarif")
                subprocess.run(
                    [
                        cli,
                        "database",
                        "analyze",
                        str(db),
                        QUERY_PACK + ":codeql-suites/" + file,
                        "--format=sarif-latest",
                        "--output=" + str(sarif),
                        "--threads=" + str(args.threads),
                        "--ram=" + str(args.ram),
                    ],
                    check=True,
                )
                results[suite] = sarif_summary(sarif)
            bqrs = dest / "structure.bqrs"
            csv = dest / "structure.csv"
            subprocess.run(
                [
                    cli,
                    "query",
                    "run",
                    str(root / "codeql/queries/WorkflowStructure.ql"),
                    "--database=" + str(db),
                    "--output=" + str(bqrs),
                    "--threads=" + str(args.threads),
                    "--ram=" + str(args.ram),
                ],
                check=True,
            )
            subprocess.run(
                [
                    cli,
                    "bqrs",
                    "decode",
                    str(bqrs),
                    "--format=csv",
                    "--output=" + str(csv),
                ],
                check=True,
            )
            models = yaml_to_model.load_models(source)
            analyses = [
                automatic.analyze(c)
                for c in discover_artifact_chains.discover_candidates(models)
            ]
            for result in analyses:
                chain = result["chain"]
                name = chain["scenario"]["id"]
                (dest / (name + ".json")).write_text(
                    json.dumps(chain, ensure_ascii=False, indent=2) + "\n"
                )
                (dest / (name + ".smv")).write_text(
                    chain_to_nusmv.render_model(chain, name + ".json")
                )
            # attestの共通構造一致が受入条件．他のcaseのfrontends差も隠さず保存する．
            compared = compare_frontends.compare(source, csv)
            record = {
                "case": case.name,
                "variant": variant,
                "confirmedIncident": manifest["confirmedExploitation"],
                "codeql": results,
                "automatic": [
                    {"status": r["status"], "candidate": r["candidate"]}
                    for r in analyses
                ],
                "frontendComparison": compared,
                "runtimeVerified": False,
            }
            summary["cases"].append(record)
            (dest / "analysis.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n"
            )
            if case.name == "actions-attest" and not compared["equivalent"]:
                raise ValueError("attest frontend mismatch")
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
