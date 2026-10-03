#!/usr/bin/env python3
"""注釈不要の限定的な成果物→権限解析．未対応はunknownとして残す．"""
import argparse
import json
import re
from pathlib import Path
import build_trust_chain
import chain_to_nusmv
import discover_artifact_chains as discovery
import yaml_to_model


def configured_authority(model, job, scope="contents"):
    # 別tokenの権限はGITHUB_TOKEN permissionsから推測できない．
    values = list(model["workflow"].get("env", {}).values()) + list(
        job.get("env", {}).values()
    )
    for step in job["steps"]:
        values.extend(step.get("env", {}).values())
        values.extend(step.get("arguments", {}).values())
    if any("secrets." in value for value in values):
        return "unknown"
    permissions = model["workflow"]["permissions"]
    # jobの指定はworkflowの指定を上書きする．{}も権限のない明示指定である．
    selected = [p for p in permissions if p["jobId"] == job["id"]]
    if not job.get("permissionsDeclared", bool(selected)):
        selected = [p for p in permissions if p["jobId"] is None]
    if any(p["access"] == "write" and p["scope"] in {scope, "*"} for p in selected):
        return "true"
    if (
        selected
        or job.get("permissionsDeclared")
        or model["workflow"].get("permissionsDeclared")
    ):
        return "false"
    return "unknown"  # repository/organization側のdefault権限を推測しない．


def fork_exclusion(condition):
    """外部forkを除外する単純なAND条件だけを認識する．OR等は証明しない．"""
    condition = condition.strip()
    if condition.startswith("${{") and condition.endswith("}}"):
        condition = condition[3:-2].strip()
    # OR/否定は限定ルール対象外．引用文字列や関数内の&&では分割しない．
    if "||" in condition or "!" in condition:
        return False
    parts, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(condition):
        c = condition[i]
        if quote:
            if c == quote:
                if i + 1 < len(condition) and condition[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
        elif depth == 0 and condition[i : i + 2] == "&&":
            parts.append(condition[start:i].strip())
            i += 2
            start = i
            continue
        i += 1
    if quote or depth:
        return False
    parts.append(condition[start:].strip())
    return any(
        re.fullmatch(
            r"github\.event\.workflow_run\.head_repository\.full_name\s*==\s*github\.repository",
            p,
        )
        for p in parts
    )


def metadata_flow(model, read):
    """単純な同一job Bashのartifact読取→step output→checkout ref→git pushを追う．

    正規表現による限定解析であり，シェル全般の意味解析ではない．候補経路には
    条件分岐の到達可能性を含めない．未対応時に安全判定を返さない．
    """
    job = next(j for j in model["workflow"]["jobs"] if j["id"] == read["jobId"])
    steps = [s for s in job["steps"] if s["index"] > read["stepIndex"]]
    path = read.get("path")
    if not path or "${{" in path.replace("${{ runner.temp }}", ""):
        return [], ["download path is not a supported literal/runner.temp path"]
    aliases, outputs, evidence = {}, set(), []
    for step in steps:
        if step["type"] != "run":
            continue
        if not step.get("commandOrderKnown") or step.get("shell") not in {"bash", "sh"}:
            continue
        script = step["command"]
        aliases.update({k: v for k, v in step.get("env", {}).items() if path in v})
        for variable, rhs in re.findall(
            r"^\s*([A-Za-z_][A-Za-z_0-9]*)=(.+)$", script, re.M
        ):
            if path in rhs:
                aliases[variable] = rhs
        tainted = set()
        for variable, rhs in re.findall(
            r"^\s*([A-Za-z_][A-Za-z_0-9]*)=(.+)$", script, re.M
        ):
            refers = path in rhs or any(
                re.search(r"\$\{?" + re.escape(a) + r"(?:\}|\b)", rhs) for a in aliases
            )
            if refers and re.search(r"\b(?:head|cat|tr)\b", rhs):
                tainted.add(variable)
                evidence.append(
                    {
                        "rule": "artifact-file-read",
                        "job": job["id"],
                        "step": step["index"],
                        "line": step["line"],
                        "variable": variable,
                    }
                )
        # echo "key=$var" >> "$GITHUB_OUTPUT" のみ認識．途中の代入・上書きは未対応．
        # 同じ変数の代入が複数ある場合，誤って最後の値を伝播させない．
        for variable in list(tainted):
            assignments = re.findall(r"^\s*" + re.escape(variable) + r"=", script, re.M)
            if len(assignments) != 1:
                tainted.remove(variable)
        for key, variable in re.findall(
            r'echo\s+["\']([A-Za-z_0-9-]+)=\$([A-Za-z_0-9]+)["\']\s*>>\s*["\']?\$GITHUB_OUTPUT',
            script,
        ):
            if variable in tainted and step.get("id"):
                outputs.add(f'steps.{step["id"]}.outputs.{key}')
    findings = []
    for output in sorted(outputs):
        checkouts = [
            s
            for s in steps
            if s.get("action") == "actions/checkout"
            and discovery.normalize_expression(s.get("arguments", {}).get("ref"))
            == output
        ]
        pushes = [
            s
            for s in steps
            if s["type"] == "run"
            and re.search(r"\bgit\s+push\b", s["command"])
            and re.search(r"\$\{\{\s*" + re.escape(output) + r"\s*\}\}", s["command"])
        ]
        for checkout in checkouts:
            for push in pushes:
                if checkout["index"] < push["index"]:
                    findings.append(
                        {
                            "rule": "artifact-selected-git-ref",
                            "output": output,
                            "checkoutStep": checkout["index"],
                            "sinkStep": push["index"],
                            "sinkLine": push["line"],
                            "sourceEvidence": evidence,
                            "scope": "syntactic potential path; execution feasibility unproven",
                        }
                    )
    return findings, (
        []
        if findings
        else [
            "no supported metadata-to-git-push path; arbitrary shell/actions remain unknown"
        ]
    )


def analyze(candidate):
    producer, consumer = candidate["producerModel"], candidate["consumerModel"]
    write, read = candidate["producerOperation"], candidate["consumerOperation"]
    job = next(j for j in consumer["workflow"]["jobs"] if j["id"] == read["jobId"])
    flows, unresolved = metadata_flow(consumer, read)
    excluded = fork_exclusion(job.get("condition", ""))
    p_events = producer["workflow"]["events"]
    external = any(
        e["name"]
        in {"pull_request", "pull_request_review", "pull_request_review_comment"}
        for e in p_events
    )
    authority = configured_authority(consumer, job)
    name_compatibility = candidate.get('nameCompatibility', 'unknown')
    unsupported_actions = [step.get('action') for step in job['steps']
                           if step['type'] == 'uses' and step.get('action') not in
                           {'actions/download-artifact', 'actions/checkout'}]
    if unsupported_actions:
        unresolved.append('unsupported consumer Action: ' + ', '.join(unsupported_actions))
    status = (
        "source-policy-blocked"
        if excluded and external
        else (
            "potential-risk"
            if flows
            and authority == "true"
            and external
            and candidate["pairingStatus"] == "unique"
            and name_compatibility == 'equal-candidate'
            and not unsupported_actions
            else "unknown"
        )
    )
    # 静的な接続は成果物の実際のID/成否を証明しない．unknownの可能な実行を検査する．
    facts = dict(
        producerUntrusted=(
            "false" if excluded and external else ("true" if external else "unknown")
        ),
        writeIntent="true",
        writeAuthorized="unknown",
        writeSucceeded="unknown",
        sameObject="false" if name_compatibility == 'literal-different' else "unknown",
        readSucceeded="unknown",
        consumerUsesObject="true" if flows else "unknown",
        integrityCheckPresent="unknown",
        integrityCheckPassed="unknown",
        privilegedConsumer="true",
        hasAuthority=authority,
    )
    # 通常のread/download成功を前提にしたpotential反例と，観測実行の反例を区別する．
    producer_endpoint = {
        "workflow": {
            "name": producer["workflow"]["name"],
            "file": producer["workflow"]["file"],
            "event": "+".join(e["name"] for e in p_events),
        },
        "operation": {
            k: write.get(k)
            for k in ["id", "kind", "jobId", "stepIndex", "name", "path", "runId"]
        },
    }
    consumer_endpoint = {
        "workflow": {
            "name": consumer["workflow"]["name"],
            "file": consumer["workflow"]["file"],
            "event": "workflow_run",
        },
        "operation": {
            k: read.get(k)
            for k in ["id", "kind", "jobId", "stepIndex", "name", "path", "runId"]
        },
    }
    use = flows[0]["checkoutStep"] if flows else read["stepIndex"]
    sink = flows[0]["sinkStep"] if flows else read["stepIndex"]
    trace = {
        stage: build_trust_chain.location(endpoint, step, meaning)
        for stage, endpoint, step, meaning in [
            (
                "start",
                producer_endpoint,
                write["stepIndex"],
                "Configured artifact upload",
            ),
            (
                "object_written",
                producer_endpoint,
                write["stepIndex"],
                "Potential successful upload, not observed",
            ),
            (
                "object_restored",
                consumer_endpoint,
                read["stepIndex"],
                "Potential same artifact download, not observed",
            ),
            (
                "integrity_checked",
                consumer_endpoint,
                read["stepIndex"],
                "Unknown integrity validation",
            ),
            (
                "object_used",
                consumer_endpoint,
                use,
                "Potential artifact-derived ref use",
            ),
            (
                "authority_reached",
                consumer_endpoint,
                sink,
                "Configured write capability, not observed write",
            ),
        ]
    }
    chain = {
        "schemaVersion": "0.3.0",
        "scenario": {
            "id": candidate["id"],
            "platform": "GitHub Actions",
            "description": "Automatically derived static over-approximation; no runtime success claimed",
            "evidenceStatus": "incomplete",
        },
        "producer": producer_endpoint,
        "consumer": consumer_endpoint,
        "sharedObject": {
            "kind": "artifact",
            "name": write["name"],
            "producerRunSelector": "current producer run",
            "consumerRunSelector": discovery.WORKFLOW_RUN_ID,
            "identityBasis": ["workflow/run-selector match; artifact name compatibility: "
                              + name_compatibility],
        },
        "facts": facts,
        "traceMap": trace,
        "evidence": [
            {
                "kind": "static",
                "source": consumer["source"]["file"],
                "claim": "Automatic limited metadata/source-policy rules",
            },
            {
                "kind": "inference",
                "source": "chain_to_nusmv.py",
                "claim": "Potential execution under unknown facts; not confirmed exploitation",
            },
        ],
        "provenance": [
            {
                "element": "facts." + key,
                "sourceKind": "static-analysis",
                "source": consumer["workflow"]["file"],
                "claim": "Automatic limited rule; "
                + ("unresolved" if value == "unknown" else "static abstraction"),
            }
            for key, value in facts.items()
        ],
    }
    recorded = {record["element"] for record in chain["provenance"]}
    chain["provenance"].extend(
        {
            "element": element,
            "sourceKind": "static-analysis",
            "source": consumer["workflow"]["file"],
            "claim": "Automatically generated structure or abstract trace location; no observed execution",
        }
        for element in sorted(discovery.required_provenance_elements(chain) - recorded)
    )
    discovery.validate_provenance(chain)
    return {
        "candidate": discovery.catalog_entry(candidate),
        "status": status,
        "configuredAuthority": authority,
        "forkSourceExcluded": excluded,
        "flows": flows,
        "unresolved": unresolved,
        "runtimeVerified": False,
        "chain": chain,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    models = yaml_to_model.load_models(args.root)
    results = [analyze(c) for c in discovery.discover_candidates(models)]
    unsupported = []
    for model in models.values():
        for job in model["workflow"]["jobs"]:
            for step in job["steps"]:
                if step.get("action", "").startswith("actions/cache") or (
                    step.get("action") == "actions/setup-python"
                    and step.get("arguments", {}).get("cache")
                ):
                    unsupported.append(
                        {
                            "file": model["workflow"]["file"],
                            "job": job["id"],
                            "step": step["index"],
                            "action": step["action"],
                            "reason": "cache namespace, key matching and historical platform policy are not modeled",
                        }
                    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "analysis.json").write_text(
        json.dumps(
            {
                "scope": "external-fork artifact metadata-to-git-ref rule only; not a repository safety verdict",
                "coverage": {
                    "workflowCount": len(models),
                    "artifactCandidates": len(results),
                    "unsupportedResources": unsupported,
                    "emptyCandidateMeaning": "unknown/unsupported; never safe",
                },
                "models": models,
                "candidates": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    for result in results:
        chain = result["chain"]
        name = chain["scenario"]["id"]
        (args.output / (name + ".json")).write_text(
            json.dumps(chain, ensure_ascii=False, indent=2) + "\n"
        )
        (args.output / (name + ".smv")).write_text(
            chain_to_nusmv.render_model(chain, name + ".json")
        )


if __name__ == "__main__":
    main()
