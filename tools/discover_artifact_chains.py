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
WORKFLOW_RUN_ID = "github.event.workflow_run.id"


def load_json(path):
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def normalize_expression(value):
    if value is None:
        return None
    value = value.strip()
    match = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", value)
    return match.group(1).strip() if match else value


def load_models(csv_path):
    with csv_path.open(newline="", encoding="utf-8") as source:
        grouped = defaultdict(list)
        for row in csv.DictReader(source):
            grouped[row["workflowName"]].append(row)
    return {
        workflow_name: codeql_csv_to_model.build_model(rows, csv_path)
        for workflow_name, rows in grouped.items()
    }


def artifact_operations(models, kind):
    for model in models.values():
        for operation in model["sharedStateOperations"]:
            if operation["kind"] == kind:
                yield model, operation


def has_event(model, event_name):
    return any(event["name"] == event_name for event in model["workflow"]["events"])


def workflow_run_targets(model):
    events = [
        event
        for event in model["workflow"]["events"]
        if event["name"] == "workflow_run"
    ]
    if len(events) != 1:
        return []
    return events[0].get("properties", {}).get("workflows", [])


def candidate_id(producer_model, producer_operation, consumer_model, consumer_operation):
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
    writes = list(artifact_operations(models, "artifactWriteIntent"))
    reads = list(artifact_operations(models, "artifactReadIntent"))
    candidates = []

    for consumer_model, read in reads:
        if not has_event(consumer_model, "workflow_run"):
            continue
        if normalize_expression(read.get("runId")) != WORKFLOW_RUN_ID:
            continue

        targets = workflow_run_targets(consumer_model)
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
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"不明な情報源区分です: {source_kind}")
    return {
        "element": element,
        "sourceKind": source_kind,
        "source": source,
        "claim": claim,
    }


def annotation_value(annotation, name):
    value = annotation["facts"][name]
    if value["sourceKind"] not in SOURCE_KINDS:
        raise ValueError(f"{name}の情報源区分が不正です")
    return value["value"]


def find_candidate(candidates, annotation):
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
    if matches[0]["pairingStatus"] != "unique":
        raise ValueError(
            f"scenario {annotation['id']}の結合候補は曖昧です．手動確認が必要です"
        )
    return matches[0]


def static_provenance(chain, csv_source):
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
    validate_provenance(chain)
    return chain


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        json.dump(value, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    candidates = discover_candidates(load_models(args.input))
    write_json(
        args.catalog,
        {
            "schemaVersion": "0.1.0",
            "source": str(args.input),
            "candidates": [catalog_entry(candidate) for candidate in candidates],
        },
    )

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
