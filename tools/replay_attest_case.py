#!/usr/bin/env python3
"""保存したattest consumerを無害な成果物とgitスタブで局所再現する．Docker内専用．"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import automatic_artifact_analysis as auto
import yaml_to_model


def replay(case, variant):
    if not Path("/.dockerenv").exists():
        raise RuntimeError(
            "Run inside a disposable Docker container with --network none"
        )
    manifest = json.loads((case / "manifest.json").read_text())
    files = [f for f in manifest["files"] if f["variant"] == variant]
    for record in files:
        if (
            hashlib.sha256((case / record["snapshot"]).read_bytes()).hexdigest()
            != record["sha256"]
        ):
            raise ValueError("Upstream snapshot checksum mismatch")
    workflow = yaml_to_model.load_models(case / variant)[
        ".github/workflows/commit-dist.yml"
    ]
    job = workflow["workflow"]["jobs"][0]
    result = {
        "variant": variant,
        "originalWorkflowSha256": next(
            f["sha256"] for f in files if f["upstreamPath"].endswith("commit-dist.yml")
        ),
        "adaptations": [
            "download/checkout actions replaced by fixture preparation",
            "GitHub expressions resolved to controlled local values",
            "git replaced by recording stub; no repository write or network",
        ],
        "scope": "local consumer-script replay, not GitHub exploitation",
        "attackerBranch": "dependabot/local-demo",
        "actualGitHubAuthorityExercised": False,
        "dummyPushReached": False,
        "steps": [],
    }
    # fork由来という局所シナリオでは，このAND条件の一項がfalseなのでjobは起動しない．
    if auto.fork_exclusion(job.get("condition", "")):
        return result | {"jobBlockedByForkOrigin": True}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        incoming = root / "incoming"
        (incoming / "dist").mkdir(parents=True)
        (incoming / "dist-meta").mkdir()
        (incoming / "dist/index.js").write_text("harmless-marker\n")
        (incoming / "dist-meta/head-ref").write_text(result["attackerBranch"] + "\n")
        (incoming / "dist-meta/head-sha").write_text("a" * 40 + "\n")
        bin = root / "bin"
        bin.mkdir()
        output = root / "output"
        output.touch()
        log = root / "git.log"
        stub = bin / "git"
        stub.write_text(
            '#!/bin/sh\ncase "$1" in\nrev-parse) printf "%s\\n" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" ;;\ndiff) exit 1 ;;\npush) printf "%s\\n" "$*" >> "$REPLAY_GIT_LOG" ;;\nconfig|add|commit) exit 0 ;;\n*) exit 2 ;;\nesac\n'
        )
        stub.chmod(0o755)
        values = {
            "runner.temp": str(root),
            "github.event.workflow_run.head_branch": "dependabot/trusted-event",
            "github.event.workflow_run.head_sha": "a" * 40,
        }
        env = {
            "PATH": str(bin) + ":/usr/bin:/bin",
            "GITHUB_OUTPUT": str(output),
            "REPLAY_GIT_LOG": str(log),
            "HOME": str(root),
        }

        def expand(value):
            def replace(match):
                key = match.group(1).strip()
                if key not in values:
                    raise ValueError("Unsupported expression " + key)
                return values[key]

            return re.sub(r"\$\{\{(.*?)\}\}", replace, value)

        for step in job["steps"]:
            condition = step.get("condition", "")
            if condition and condition != "steps.check.outputs.present == 'true'":
                raise ValueError("Unsupported replay step condition")
            if condition and values.get("steps.check.outputs.present") != "true":
                continue
            if step["type"] != "run":
                continue
            before = output.read_text()
            completed = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", expand(step["command"])],
                cwd=root,
                env=env | {k: expand(v) for k, v in step.get("env", {}).items()},
                text=True,
                capture_output=True,
                timeout=10,
            )
            result["steps"].append(
                {
                    "index": step["index"],
                    "line": step["line"],
                    "exitCode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
            if completed.returncode:
                raise RuntimeError(json.dumps(result))
            for line in output.read_text()[len(before) :].splitlines():
                key, separator, value = line.partition("=")
                if not separator or not re.fullmatch(r"[A-Za-z_0-9-]+", key):
                    raise ValueError("Unsupported output encoding")
                values[f'steps.{step["id"]}.outputs.{key}'] = value
        result["dummyPushCommands"] = (
            log.read_text().splitlines() if log.exists() else []
        )
        result["dummyPushReached"] = bool(result["dummyPushCommands"])
        result["harmlessBundleMoved"] = (
            root / "dist/index.js"
        ).read_text() == "harmless-marker\n"
    return result | {"jobBlockedByForkOrigin": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("case", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    results = [replay(args.case, variant) for variant in ["vulnerable", "fixed"]]
    if not results[0]["dummyPushReached"] or results[1]["dummyPushReached"]:
        raise RuntimeError("Unexpected replay results")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
