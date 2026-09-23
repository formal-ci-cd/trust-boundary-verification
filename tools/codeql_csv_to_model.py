#!/usr/bin/env python3
"""CodeQLの表形式出力をCI/CD共通モデルへ変換する．"""

import argparse
import csv
import json
from pathlib import Path


SCHEMA_VERSION = "0.1.0"


def parse_event_detail(detail):
    """CodeQLが`;`区切りで出した起動可能性と権限区分を真偽値へ戻す．"""
    values = set(detail.split(";"))
    return {
        "externallyTriggerable": "externally-triggerable" in values,
        "privileged": "privileged" in values,
    }


def split_action(detail):
    """`所有者/Action@version`をAction名とversionへ分ける．"""
    # digest固定ではversion部分にも記号が含まれ得るため，最後の`@`で分割する．
    action, separator, version = detail.rpartition("@")
    if not separator:
        return detail, ""
    return action, version


def load_rows(input_path, workflow_name):
    """CSV全体から，指定されたワークフローに属する行だけを読み込む．"""
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
    """1ワークフロー分のCSV行を，研究用の共通JSON構造へ組み直す．"""
    workflow_row = next(row for row in rows if row["kind"] == "workflow")

    # CSVでは全てが独立した行になっているため，種類ごとの一時領域へ振り分ける．
    events = []
    event_properties = []
    permissions = []
    jobs = {}
    expressions = []
    steps = {}

    for row in rows:
        kind = row["kind"]
        job_id = row["jobId"]
        step_index = row["stepIndex"]

        # event本体を先に作る．workflowsやtypesなどの付随情報は後から結合する．
        if kind == "event":
            events.append(
                {
                    "name": row["name"],
                    **parse_event_detail(row["detail"]),
                    "properties": {},
                }
            )
        elif kind == "event-property":
            event_name, property_name = row["name"].split(".", 1)
            event_properties.append((event_name, property_name, row["detail"]))
        # permissionはワークフロー全体とjob単位の両方があるため，jobIdも保持する．
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
            # 複数のrunner labelが抽出された場合，現在は後から来た値で上書きされる．
            # 今回の4構成は単一labelなので影響しないが，一般化時には配列へ追記する必要がある．
            jobs[job_id] = {
                "id": job_id,
                "runnerLabels": [row["detail"].removeprefix("runs-on=")],
                "steps": [],
            }
        # uses-stepとuses-argumentはCSV上で別行なので，同じ(jobId, stepIndex)へ統合する．
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
        # シェルコマンドは文字列として保持する．ここでは処理の意味までは解釈しない．
        # 現在は同じstepから複数行が出ると後の行で上書きされるため，CSVの全情報を
        # 共通JSONへ保持できていない．シェルの自動解釈へ進む前に修正が必要である．
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

    # event-propertyを対応するeventへ戻す．同名eventが複数ある場合は誤結合を避けて停止する．
    for event_name, property_name, value in event_properties:
        matches = [event for event in events if event["name"] == event_name]
        if len(matches) != 1:
            raise ValueError(f"event propertyの所属を特定できません: {event_name}")
        matches[0]["properties"].setdefault(property_name, []).append(value)

    # step番号順に並べ，対応するjobのstepsへ格納する．
    for (job_id, _), step in sorted(steps.items(), key=lambda item: (item[0][0], int(item[0][1]))):
        if job_id in jobs:
            jobs[job_id]["steps"].append(step)

    # Action名から，共有状態に対する保存又は取得の「記述」を研究用の操作へ変換する．
    # Intentは実行成功を意味しない．実行時の許可及び成否は別の観測情報として扱う．
    shared_state_operations = []
    for job in jobs.values():
        for step in job["steps"]:
            # actions/cacheは復元後に保存も行い得るため，読込みと書込みの両方へ該当する．
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
            # 成果物はnameとpathに加え，取得側で起動元runを識別するrunIdも保持する．
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

    # 静的解析だけで決められない検証事実は，安全側へ倒さずunknownとして初期化する．
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
    """GitHub Actionsの実行結果を追加し，参照先の操作IDが実在するか確認する．"""
    operation_ids = {operation["id"] for operation in model["sharedStateOperations"]}
    referenced_ids = {
        result["operationId"] for result in observation.get("operationResults", [])
    }
    unknown_ids = referenced_ids - operation_ids
    if unknown_ids:
        raise ValueError(f"観測記録が未知の操作を参照しています: {sorted(unknown_ids)}")
    model["runtimeObservations"].append(observation)


def main():
    """コマンドライン引数を読み，指定したワークフローの共通JSONを出力する．"""
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
