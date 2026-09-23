#!/usr/bin/env python3
"""CI/CD共通モデルをNuSMVの有限状態モデルへ変換する．"""

import argparse
import json
from pathlib import Path


UNKNOWN = "unknown"

# 初期のキャッシュ実験で状態遷移へ使用する事実名を固定する．
FACT_NAMES = (
    "write_intent",
    "write_authorized",
    "write_succeeded",
    "same_object",
    "consumer_uses_object",
    "integrity_verified",
    "privileged_consumer",
    "has_authority",
)


def boolean_text(value):
    """Pythonの真偽値をSMVの定数表記へ変換する．"""
    return "TRUE" if value else "FALSE"


def select_operation(model, operation_id=None):
    """SMVの起点とするキャッシュ保存操作を1件選ぶ．"""
    # IDが省略された場合は，モデル内のcacheWriteIntentが1件であることを要求する．
    operations = model["sharedStateOperations"]
    if operation_id:
        matches = [operation for operation in operations if operation["id"] == operation_id]
    else:
        matches = [operation for operation in operations if operation["kind"] == "cacheWriteIntent"]
    if len(matches) != 1:
        raise ValueError("対象のキャッシュ保存操作を1件に特定できません")
    if matches[0]["kind"] != "cacheWriteIntent":
        raise ValueError("NuSMVモデルの起点にはキャッシュ保存操作が必要です")
    return matches[0]


def select_observation(model, observation_id=None):
    """変換へ使用するGitHub Actionsの実行時観測を1件選ぶ．"""
    observations = model["runtimeObservations"]
    if observation_id:
        matches = [observation for observation in observations if observation["id"] == observation_id]
        if len(matches) != 1:
            raise ValueError("指定された実行時観測を1件に特定できません")
        return matches[0]
    if len(observations) > 1:
        raise ValueError("実行時観測が複数あるため，対象IDを指定してください")
    return observations[0] if observations else None


def runtime_facts(operation, observation):
    """実行時観測から，保存許可と保存成功の値を取得する．"""
    # 観測がなければ失敗と決めず，未確認を表すunknownを返す．
    if observation is None:
        return UNKNOWN, UNKNOWN

    # GitHub側の許可判定と，実際のActionの成否を別の事実として扱う．
    decision = observation["platformPolicy"]["cacheWriteDecision"]
    write_authorized = {
        "allowed": True,
        "denied": False,
        "unknown": UNKNOWN,
    }[decision]

    results = [
        result
        for result in observation["operationResults"]
        if result["operationId"] == operation["id"]
    ]
    if len(results) > 1:
        raise ValueError("同じ操作に対する実行結果が複数あります")
    if not results:
        return write_authorized, UNKNOWN
    write_succeeded = {
        "succeeded": True,
        "failed": False,
        "unknown": UNKNOWN,
    }[results[0]["outcome"]]
    return write_authorized, write_succeeded


def extract_facts(model, operation_id=None, observation_id=None):
    """共通JSONと実行時観測から，SMVで使用する全事実をまとめる．"""
    operation = select_operation(model, operation_id)
    observation = select_observation(model, observation_id)
    write_authorized, write_succeeded = runtime_facts(operation, observation)
    verification = model["verificationFacts"]

    return {
        "write_intent": operation["kind"] == "cacheWriteIntent",
        "write_authorized": write_authorized,
        "write_succeeded": write_succeeded,
        "same_object": verification["sameObject"],
        "consumer_uses_object": verification["consumerUsesObject"],
        "integrity_verified": verification["integrityVerified"],
        "privileged_consumer": verification["privilegedConsumer"],
        "has_authority": verification["hasAuthority"],
    }


def render_model(facts, source_name):
    """初期のキャッシュ実験用の事実からSMVテキストを生成する．"""
    # unknownはFROZENVAR，確認済みの値はDEFINEへ分けて出力する．
    unknown_facts = [name for name in FACT_NAMES if facts[name] == UNKNOWN]
    known_facts = [name for name in FACT_NAMES if facts[name] != UNKNOWN]
    lines = [
        f"-- Generated from {source_name}",
        "-- unknownはFROZENVARとし，実行中は一定だが真偽の両方を検査する．",
        "MODULE main",
    ]

    if unknown_facts:
        lines.append("FROZENVAR")
        lines.extend(f"  {name} : boolean;" for name in unknown_facts)

    # キャッシュ保存から権限到達までの簡略化した状態遷移を定義する．
    lines.extend(
        [
            "VAR",
            "  stage : {start, object_written, object_restored, object_used, integrity_checked, authority_reached, blocked};",
        ]
    )

    if known_facts:
        lines.append("DEFINE")
        lines.extend(f"  {name} := {boolean_text(facts[name])};" for name in known_facts)

    lines.extend(
        [
            "INVAR write_succeeded -> (write_intent & write_authorized)",
            "ASSIGN",
            "  init(stage) := start;",
            "  next(stage) := case",
            "    stage = start & (!write_intent | !write_authorized | !write_succeeded) : blocked;",
            "    stage = start : object_written;",
            "    stage = object_written & same_object : object_restored;",
            "    stage = object_written : blocked;",
            "    stage = object_restored & consumer_uses_object : object_used;",
            "    stage = object_restored : blocked;",
            "    stage = object_used & integrity_verified : integrity_checked;",
            "    stage = object_used & privileged_consumer & has_authority : authority_reached;",
            "    stage = object_used : blocked;",
            "    TRUE : stage;",
            "  esac;",
            "",
            "CTLSPEC AG stage != authority_reached",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    """共通JSONを読み，初期キャッシュ実験用のSMVファイルを書き出す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--observation-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as source:
        model = json.load(source)
    facts = extract_facts(model, args.operation_id, args.observation_id)
    output = render_model(facts, str(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
