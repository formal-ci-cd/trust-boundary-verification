#!/usr/bin/env python3
"""CodeQLの表形式出力をCI/CD共通モデルへ変換する．"""

import argparse
import csv
import json
from pathlib import Path


SCHEMA_VERSION = "0.1.0"


def parse_event_detail(detail):
    values = set(detail.split(";"))
    return {
        "externallyTriggerable": "externally-triggerable" in values,
        "privileged": "privileged" in values,
    }


def split_action(detail):
    action, separator, version = detail.rpartition("@")
    if not separator:
        return detail, ""
    return action, version


def load_rows(input_path, workflow_name):
    with input_path.open(newline="", encoding="utf-8") as source:
        rows = [
            row
            for row in csv.DictReader(source)
            if row["workflowName"] == workflow_name
        ]
    if not rows:
        raise ValueError(f"workflowが見つかりません: {workflow_name}")
    return rows


def build_model(rows, input_path):
    workflow_row = next(row for row in rows if row["kind"] == "workflow")
    events = []
    permissions = []
    jobs = {}
    expressions = []
    steps = {}

    for row in rows:
        kind = row["kind"]
        job_id = row["jobId"]
        step_index = row["stepIndex"]

        if kind == "event":
            events.append({"name": row["name"], **parse_event_detail(row["detail"])})
        elif kind == "permission":
            scope, access = row["detail"].split(":", 1)
            permissions.append(
                {
                    "jobId": None if job_id == "-" else job_id,
                    "scope": scope.strip(),
                    "access": access.strip(),
                }
            )
        elif kind == "job":
            jobs[job_id] = {
                "id": job_id,
                "runnerLabels": [row["detail"].removeprefix("runs-on=")],
                "steps": [],
            }
        elif kind == "uses-step":
            action, version = split_action(row["detail"])
            step = steps.setdefault(
                (job_id, step_index),
                {
                    "index": int(step_index),
                    "type": "uses",
                    "action": "",
                    "version": "",
                    "arguments": {},
                },
            )
            step["action"] = action
            step["version"] = version
        elif kind == "uses-argument":
            step = steps.setdefault(
                (job_id, step_index),
                {
                    "index": int(step_index),
                    "type": "uses",
                    "action": "",
                    "version": "",
                    "arguments": {},
                },
            )
            step["arguments"][row["name"]] = row["detail"]
        elif kind == "run-step":
            steps[(job_id, step_index)] = {
                "index": int(step_index),
                "type": "run",
                "command": row["detail"],
            }
        elif kind == "expression":
            expressions.append(
                {"raw": row["name"], "normalized": row["detail"], "line": int(row["line"])}
            )

    for (job_id, _), step in sorted(steps.items(), key=lambda item: (item[0][0], int(item[0][1]))):
        if job_id in jobs:
            jobs[job_id]["steps"].append(step)

    shared_state_operations = []
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("action") in {"actions/cache", "actions/cache/save"}:
                shared_state_operations.append(
                    {
                        "id": f"cache-write:{job['id']}:{step['index']}",
                        "kind": "cacheWriteIntent",
                        "jobId": job["id"],
                        "stepIndex": step["index"],
                        "key": step["arguments"].get("key"),
                        "path": step["arguments"].get("path"),
                    }
                )
            if step.get("action") in {"actions/cache", "actions/cache/restore"}:
                shared_state_operations.append(
                    {
                        "id": f"cache-read:{job['id']}:{step['index']}",
                        "kind": "cacheReadIntent",
                        "jobId": job["id"],
                        "stepIndex": step["index"],
                        "key": step["arguments"].get("key"),
                        "path": step["arguments"].get("path"),
                    }
                )
            if step.get("action") == "actions/upload-artifact":
                shared_state_operations.append(
                    {
                        "id": f"artifact-write:{job['id']}:{step['index']}",
                        "kind": "artifactWriteIntent",
                        "jobId": job["id"],
                        "stepIndex": step["index"],
                        "key": None,
                        "path": step["arguments"].get("path"),
                        "name": step["arguments"].get("name"),
                        "runId": None,
                    }
                )
            if step.get("action") == "actions/download-artifact":
                shared_state_operations.append(
                    {
                        "id": f"artifact-read:{job['id']}:{step['index']}",
                        "kind": "artifactReadIntent",
                        "jobId": job["id"],
                        "stepIndex": step["index"],
                        "key": None,
                        "path": step["arguments"].get("path"),
                        "name": step["arguments"].get("name"),
                        "runId": step["arguments"].get("run-id"),
                    }
                )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": {
            "type": "codeqlTable",
            "file": str(input_path),
            "staticAnalysisOnly": True,
        },
        "workflow": {
            "name": workflow_row["name"],
            "file": workflow_row["filePath"],
            "events": events,
            "permissions": permissions,
            "jobs": list(jobs.values()),
            "expressions": expressions,
        },
        "sharedStateOperations": shared_state_operations,
        "verificationFacts": {
            "sameObject": "unknown",
            "consumerUsesObject": "unknown",
            "integrityVerified": "unknown",
            "privilegedConsumer": "unknown",
            "hasAuthority": "unknown",
        },
        "runtimeObservations": [],
    }


def add_observation(model, observation):
    operation_ids = {operation["id"] for operation in model["sharedStateOperations"]}
    referenced_ids = {
        result["operationId"] for result in observation.get("operationResults", [])
    }
    unknown_ids = referenced_ids - operation_ids
    if unknown_ids:
        raise ValueError(f"観測記録が未知の操作を参照しています: {sorted(unknown_ids)}")
    model["runtimeObservations"].append(observation)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    model = build_model(load_rows(args.input, args.workflow), args.input)
    if args.observation:
        with args.observation.open(encoding="utf-8") as source:
            add_observation(model, json.load(source))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        json.dump(model, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


if __name__ == "__main__":
    main()
