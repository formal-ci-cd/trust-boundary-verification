#!/usr/bin/env python3
"""CodeQL由来のworkflowモデルを，複数run間の信頼経路へ結合する．"""

import argparse
import json
from pathlib import Path


TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"


def load_json(path):
    """UTF-8のJSONファイルを読み込む．"""
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def find_operation(model, operation_id):
    """共通モデルから指定IDの共有状態操作を重複なく1件取得する．"""
    matches = [
        operation
        for operation in model["sharedStateOperations"]
        if operation["id"] == operation_id
    ]
    if len(matches) != 1:
        raise ValueError(f"共有状態操作を1件に特定できません: {operation_id}")
    return matches[0]


def first_event(model):
    """実験モデルの起動契機を1件取得する．複数あるモデルは誤解釈を避けて停止する．"""
    events = model["workflow"]["events"]
    if len(events) != 1:
        raise ValueError("起動契機を1件に特定できません")
    return events[0]


def endpoint(model, operation):
    """ワークフローと共有状態操作から，信頼経路の片側を表す情報を作る．"""
    workflow = model["workflow"]
    event = first_event(model)
    return {
        "workflow": {
            "name": workflow["name"],
            "file": workflow["file"],
            "event": event["name"],
        },
        "operation": {
            "id": operation["id"],
            "kind": operation["kind"],
            "jobId": operation["jobId"],
            "stepIndex": operation["stepIndex"],
            "name": operation.get("name"),
            "path": operation.get("path"),
            "runId": operation.get("runId"),
        },
    }


def location(endpoint_value, step_index, meaning, job_id=None):
    """状態遷移を元のワークフロー，job及びstepへ戻すための位置情報を作る．"""
    return {
        "workflowFile": endpoint_value["workflow"]["file"],
        "workflowName": endpoint_value["workflow"]["name"],
        "jobId": job_id or endpoint_value["operation"]["jobId"],
        "stepIndex": step_index,
        "meaning": meaning,
    }


def build_chain(config, producer_model, consumer_model):
    """保存側と取得側の共通モデルを，1本の信頼経路へ結合する．"""
    # 設定が指す保存操作と取得操作を各モデルから選ぶ．
    producer_operation = find_operation(
        producer_model, config["producerOperationId"]
    )
    consumer_operation = find_operation(
        consumer_model, config["consumerOperationId"]
    )
    producer = endpoint(producer_model, producer_operation)
    consumer = endpoint(consumer_model, consumer_operation)
    producer_event = first_event(producer_model)
    consumer_event = first_event(consumer_model)

    # 起動契機に対するCodeQLの分類から，保存側と取得側の信頼区分を作る．
    producer_untrusted = (
        TRUE
        if producer_event["externallyTriggerable"] and not producer_event["privileged"]
        else FALSE
    )
    privileged_consumer = TRUE if consumer_event["privileged"] else FALSE

    # 成果物名が異なる場合，同じobjectとして接続してはいけないため停止する．
    object_name = producer_operation.get("name")
    if object_name != consumer_operation.get("name"):
        raise ValueError("producerとconsumerの共有object名が一致しません")

    # 静的に導ける値と，実行結果又は人手判断から与えた値を1か所へまとめる．
    configured_facts = config["facts"]
    facts = {
        "producerUntrusted": producer_untrusted,
        "writeIntent": TRUE,
        "writeAuthorized": configured_facts["writeAuthorized"],
        "writeSucceeded": configured_facts["writeSucceeded"],
        "sameObject": configured_facts["sameObject"],
        "readSucceeded": configured_facts["readSucceeded"],
        "consumerUsesObject": configured_facts["consumerUsesObject"],
        "integrityCheckPresent": configured_facts["integrityCheckPresent"],
        "integrityCheckPassed": configured_facts["integrityCheckPassed"],
        "privilegedConsumer": privileged_consumer,
        "hasAuthority": configured_facts["hasAuthority"],
    }

    # NuSMVの反例に出る各状態を，元のYAML上の位置へ対応付ける．
    authority = config["authority"]
    trace_map = {
        "start": location(
            producer,
            producer_operation["stepIndex"],
            "未信頼なworkflowが共有objectへの保存を試みる．",
        ),
        "object_written": location(
            producer,
            producer_operation["stepIndex"],
            "共有objectの保存が成功する．",
        ),
        "object_restored": location(
            consumer,
            consumer_operation["stepIndex"],
            "同じ共有objectを後続workflowが取得する．",
        ),
        "object_used": location(
            consumer,
            config["useStepIndex"],
            "取得した内容を後続処理が利用する．",
        ),
        "integrity_checked": location(
            consumer,
            config["integrityStepIndex"],
            "復元した内容を利用する前に完全性を確認する．",
        ),
        "authority_reached": location(
            consumer,
            authority["stepIndex"],
            authority["meaning"],
            authority["jobId"],
        ),
    }

    # 保存側で確認した成果物名を，共有objectの正式な名前として記録する．
    shared_object = dict(config["sharedObject"])
    shared_object["name"] = object_name

    return {
        "schemaVersion": config["schemaVersion"],
        "scenario": config["scenario"],
        "producer": producer,
        "consumer": consumer,
        "sharedObject": shared_object,
        "facts": facts,
        "traceMap": trace_map,
        "evidence": config["evidence"],
    }


def main():
    """設定と2個の共通モデルを読み，信頼経路JSONを書き出す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = load_json(args.input)
    producer_model = load_json(args.root / config["producerModel"])
    consumer_model = load_json(args.root / config["consumerModel"])
    chain = build_chain(config, producer_model, consumer_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as destination:
        json.dump(chain, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


if __name__ == "__main__":
    main()
