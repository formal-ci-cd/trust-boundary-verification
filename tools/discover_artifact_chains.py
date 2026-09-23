#!/usr/bin/env python3
"""CodeQLの抽出表から，workflowをまたぐartifact経路を自動生成する．"""

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import build_trust_chain
import codeql_csv_to_model


SOURCE_KINDS = {"static-analysis", "runtime-observation", "manual-judgment"}
# 取得側が起動元のrunを明示しているか判定するため，正規化後の式を定数化する．
WORKFLOW_RUN_ID = "github.event.workflow_run.id"


def load_json(path):
    """UTF-8のJSONファイルを読み込む．"""
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def normalize_expression(value):
    """`${{ ... }}`の外側を除き，式の空白差を吸収して比較可能にする．"""
    if value is None:
        return None
    value = value.strip()
    match = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", value)
    return match.group(1).strip() if match else value


def load_models(csv_path):
    """CodeQLのCSVをワークフロー名で分け，全ワークフローの共通モデルを作る．"""
    with csv_path.open(newline="", encoding="utf-8") as source:
        grouped = defaultdict(list)
        for row in csv.DictReader(source):
            grouped[row["workflowName"]].append(row)
    return {
        workflow_name: codeql_csv_to_model.build_model(rows, csv_path)
        for workflow_name, rows in grouped.items()
    }


def artifact_operations(models, kind):
    """全モデルから，指定した種類の成果物操作を順番に取り出す．"""
    for model in models.values():
        for operation in model["sharedStateOperations"]:
            if operation["kind"] == kind:
                yield model, operation


def has_event(model, event_name):
    """ワークフローが指定された起動契機を持つか確認する．"""
    return any(event["name"] == event_name for event in model["workflow"]["events"])


def workflow_run_targets(model):
    """workflow_runの`workflows:`へ指定された起動元ワークフロー名を返す．"""
    events = [
        event
        for event in model["workflow"]["events"]
        if event["name"] == "workflow_run"
    ]
    if len(events) != 1:
        return []
    return events[0].get("properties", {}).get("workflows", [])


def candidate_id(producer_model, producer_operation, consumer_model, consumer_operation):
    """保存側と取得側の組合せから，再現可能な短い候補IDを作る．"""
    # ファイルと操作IDが同じなら何度実行しても同じIDになる．
    identity = "|".join(
        [
            producer_model["workflow"]["file"],
            producer_operation["id"],
            consumer_model["workflow"]["file"],
            consumer_operation["id"],
        ]
    )
    return "artifact-" + hashlib.sha256(identity.encode()).hexdigest()[:12]


def discover_candidates(models):
    """三つの静的条件を満たす成果物の保存側と取得側を対応付ける．"""
    writes = list(artifact_operations(models, "artifactWriteIntent"))
    reads = list(artifact_operations(models, "artifactReadIntent"))
    candidates = []

    for consumer_model, read in reads:
        # 今回は別ワークフローの完了後に動く取得側だけを対象にする．
        if not has_event(consumer_model, "workflow_run"):
            continue
        # 起動元以外の固定runなどを取得する構成は，今回の自動結合対象から除外する．
        if normalize_expression(read.get("runId")) != WORKFLOW_RUN_ID:
            continue

        targets = workflow_run_targets(consumer_model)
        # 成果物名とworkflow_runの起動元名が両方一致する保存操作を探す．
        matches = [
            (producer_model, write)
            for producer_model, write in writes
            if write.get("name") is not None and write.get("name") == read.get("name")
            and producer_model["workflow"]["name"] in targets
        ]
        for producer_model, write in matches:
            candidates.append(
                {
                    "id": candidate_id(
                        producer_model, write, consumer_model, read
                    ),
                    "producerModel": producer_model,
                    "producerOperation": write,
                    "consumerModel": consumer_model,
                    "consumerOperation": read,
                    # 複数の保存側が一致した場合は，誤って1件を選ばず曖昧として残す．
                    "pairingStatus": "unique" if len(matches) == 1 else "ambiguous",
                    "matchBasis": [
                        "artifact nameが一致する．",
                        "取得側が起動元workflow_runのrun IDを指定する．",
                        "workflow_runのworkflows指定が保存側workflow名と一致する．",
                    ],
                }
            )
    return candidates


def catalog_entry(candidate):
    """内部処理用の候補から，確認用catalogへ保存する項目だけを取り出す．"""
    return {
        "id": candidate["id"],
        "pairingStatus": candidate["pairingStatus"],
        "producer": {
            "workflow": candidate["producerModel"]["workflow"]["name"],
            "file": candidate["producerModel"]["workflow"]["file"],
            "operationId": candidate["producerOperation"]["id"],
        },
        "consumer": {
            "workflow": candidate["consumerModel"]["workflow"]["name"],
            "file": candidate["consumerModel"]["workflow"]["file"],
            "operationId": candidate["consumerOperation"]["id"],
        },
        "sharedObject": {
            "kind": "artifact",
            "name": candidate["producerOperation"].get("name"),
            "consumerRunSelector": normalize_expression(
                candidate["consumerOperation"].get("runId")
            ),
            "triggerWorkflow": candidate["producerModel"]["workflow"]["name"],
        },
        "matchBasis": candidate["matchBasis"],
    }


def source_record(element, source_kind, source, claim):
    """モデル要素がどの情報源から得られたかを表す1件の記録を作る．"""
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"不明な情報源区分です: {source_kind}")
    return {
        "element": element,
        "sourceKind": source_kind,
        "source": source,
        "claim": claim,
    }


def annotation_value(annotation, name):
    """注釈から事実値を取得し，情報源区分が正しいことも確認する．"""
    value = annotation["facts"][name]
    if value["sourceKind"] not in SOURCE_KINDS:
        raise ValueError(f"{name}の情報源区分が不正です")
    return value["value"]


def find_candidate(candidates, annotation):
    """注釈に書かれた保存側と取得側へ一致する一意な候補を選ぶ．"""
    matches = [
        candidate
        for candidate in candidates
        if candidate["producerModel"]["workflow"]["name"]
        == annotation["producerWorkflow"]
        and candidate["consumerModel"]["workflow"]["name"]
        == annotation["consumerWorkflow"]
    ]
    if len(matches) != 1:
        raise ValueError(
            f"scenario {annotation['id']}の結合候補を1件に特定できません: {len(matches)}件"
        )
    # 自動結合が曖昧な場合，人手注釈があっても自動的には先へ進めない．
    if matches[0]["pairingStatus"] != "unique":
        raise ValueError(
            f"scenario {annotation['id']}の結合候補は曖昧です．手動確認が必要です"
        )
    return matches[0]


def static_provenance(chain, csv_source):
    """CodeQL抽出結果から作ったモデル要素へ情報源を付ける．"""
    records = []
    for side in ("producer", "consumer"):
        for section, values in chain[side].items():
            for name in values:
                records.append(
                    source_record(
                        f"{side}.{section}.{name}",
                        "static-analysis",
                        csv_source,
                        "CodeQLの抽出表から取得した．",
                    )
                )
    for name in chain["sharedObject"]:
        if name == "producerRunSelector":
            # upload-artifactが現在のrunへ保存するという意味は，Action仕様の解釈を含む．
            records.append(
                source_record(
                    f"sharedObject.{name}",
                    "manual-judgment",
                    "tools/discover_artifact_chains.py",
                    "upload-artifactは実行中runへ成果物を保存すると解釈した．",
                )
            )
        else:
            records.append(
                source_record(
                    f"sharedObject.{name}",
                    "static-analysis",
                    csv_source,
                    "保存操作と取得操作の照合から生成した．",
                )
            )
    return records


def required_provenance_elements(chain):
    """情報源を必ず1件持つべきモデル要素の一覧を作る．"""
    required = {
        "scenario.id",
        "scenario.platform",
        "scenario.description",
        "scenario.evidenceStatus",
    }
    for side in ("producer", "consumer"):
        for section, values in chain[side].items():
            required.update(f"{side}.{section}.{name}" for name in values)
    required.update(f"sharedObject.{name}" for name in chain["sharedObject"])
    required.update(f"facts.{name}" for name in chain["facts"])
    required.update(f"traceMap.{name}" for name in chain["traceMap"])
    return required


def validate_provenance(chain):
    """情報源の重複，不足及び不正な区分がないことを確認する．"""
    elements = [record["element"] for record in chain["provenance"]]
    duplicates = {element for element in elements if elements.count(element) > 1}
    if duplicates:
        raise ValueError(f"情報源が重複しています: {sorted(duplicates)}")
    missing = required_provenance_elements(chain) - set(elements)
    if missing:
        raise ValueError(f"情報源が不足しています: {sorted(missing)}")
    invalid = {
        record["sourceKind"]
        for record in chain["provenance"]
        if record["sourceKind"] not in SOURCE_KINDS
    }
    if invalid:
        raise ValueError(f"不明な情報源区分です: {sorted(invalid)}")


def build_annotated_chain(candidate, annotation, csv_source, annotation_source):
    """自動結合候補へ実行結果と人手判断を加え，検証可能な信頼経路を作る．"""
    # この一覧の値はCodeQLだけでは確定しないため，注釈側に根拠付きで記録する．
    fact_names = [
        "writeAuthorized",
        "writeSucceeded",
        "readSucceeded",
        "consumerUsesObject",
        "integrityCheckPresent",
        "integrityCheckPassed",
        "hasAuthority",
    ]
    missing = set(fact_names) - set(annotation["facts"])
    if missing:
        raise ValueError(f"scenario {annotation['id']}の事実が不足しています: {sorted(missing)}")

    # build_trust_chainが受け取る形式へ，自動抽出値と注釈値をまとめる．
    config = {
        "schemaVersion": "0.3.0",
        "scenario": {
            "id": annotation["id"],
            "platform": "GitHub Actions",
            "description": annotation["description"],
            "evidenceStatus": annotation["evidenceStatus"],
        },
        "producerOperationId": candidate["producerOperation"]["id"],
        "consumerOperationId": candidate["consumerOperation"]["id"],
        "facts": {
            name: annotation_value(annotation, name) for name in fact_names
        }
        # 三つの結合条件が成立した候補なので，静的モデル上は同じ成果物として扱う．
        # これは実際のrunでartifact IDの一致を確認したという意味ではない．
        | {"sameObject": "true"},
        "sharedObject": {
            "kind": "artifact",
            "producerRunSelector": "github.run_id",
            "consumerRunSelector": WORKFLOW_RUN_ID,
            "identityBasis": candidate["matchBasis"],
        },
        "useStepIndex": annotation["locations"]["objectUsed"]["stepIndex"],
        "integrityStepIndex": annotation["locations"]["integrityChecked"]["stepIndex"],
        "authority": {
            "jobId": annotation["locations"]["authorityReached"]["jobId"],
            "stepIndex": annotation["locations"]["authorityReached"]["stepIndex"],
            "meaning": annotation["locations"]["authorityReached"]["claim"],
        },
        "evidence": [
            {
                "kind": "static",
                "source": csv_source,
                "claim": "CodeQL抽出結果から保存側と取得側を自動結合した．",
            }
        ],
    }
    chain = build_trust_chain.build_chain(
        config, candidate["producerModel"], candidate["consumerModel"]
    )

    # 各モデル要素について，静的解析，実行結果又は人手判断のいずれかを明示する．
    provenance = [
        source_record(
            f"scenario.{name}",
            "manual-judgment",
            annotation_source,
            "評価scenarioの定義から設定した．",
        )
        for name in chain["scenario"]
    ]
    provenance.extend(static_provenance(chain, csv_source))
    for name in ("producerUntrusted", "writeIntent", "sameObject", "privilegedConsumer"):
        provenance.append(
            source_record(
                f"facts.{name}",
                "static-analysis",
                csv_source,
                "CodeQL抽出結果と結合条件から算出した．",
            )
        )
    for name in fact_names:
        fact = annotation["facts"][name]
        provenance.append(
            source_record(
                f"facts.{name}", fact["sourceKind"], fact["source"], fact["claim"]
            )
        )
    for stage in ("start", "object_written", "object_restored"):
        provenance.append(
            source_record(
                f"traceMap.{stage}",
                "static-analysis",
                csv_source,
                "CodeQLが抽出した操作位置から生成した．",
            )
        )
    for stage, key in (
        ("object_used", "objectUsed"),
        ("integrity_checked", "integrityChecked"),
        ("authority_reached", "authorityReached"),
    ):
        location = annotation["locations"][key]
        provenance.append(
            source_record(
                f"traceMap.{stage}",
                location["sourceKind"],
                location["source"],
                location["claim"],
            )
        )
    chain["provenance"] = provenance
    # 根拠のない値又は二重に根拠を与えた値が混入した場合は出力前に停止する．
    validate_provenance(chain)
    return chain


def write_json(path, value):
    """親directoryを作成し，読みやすい形式でJSONを書き出す．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        json.dump(value, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


def main():
    """候補catalogを生成し，注釈指定時は各信頼経路JSONも生成する．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    # 最初に全候補を出力し，どのワークフロー同士が結ばれたか確認可能にする．
    candidates = discover_candidates(load_models(args.input))
    write_json(
        args.catalog,
        {
            "schemaVersion": "0.1.0",
            "source": str(args.input),
            "candidates": [catalog_entry(candidate) for candidate in candidates],
        },
    )

    # 注釈が指定された場合だけ，意味及び実行結果を加えた検証用モデルまで生成する．
    if args.annotations:
        if not args.output_dir:
            parser.error("--annotationsには--output-dirが必要です")
        annotations = load_json(args.annotations)
        for annotation in annotations["scenarios"]:
            candidate = find_candidate(candidates, annotation)
            chain = build_annotated_chain(
                candidate, annotation, str(args.input), str(args.annotations)
            )
            write_json(args.output_dir / f"{annotation['id'].lower()}.json", chain)


if __name__ == "__main__":
    main()
