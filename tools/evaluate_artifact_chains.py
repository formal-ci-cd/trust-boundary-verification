#!/usr/bin/env python3
"""自動生成したartifact経路をNuSMVで検査し，期待値との一致を集計する．"""

import argparse
import json
import subprocess
from pathlib import Path

import chain_to_nusmv


def load_json(path):
    """UTF-8のJSONファイルを読み込む．"""
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def parse_verdict(output):
    """NuSMVの検査式から，研究上のsafe又はunsafeへ変換する．"""
    property_line = next(
        (
            line
            for line in output.splitlines()
            if line.startswith("-- specification AG !")
        ),
        None,
    )
    if property_line is None:
        raise ValueError("NuSMVの検査結果を取得できません")
    if property_line.endswith(" is false"):
        return "unsafe"
    if property_line.endswith(" is true"):
        return "safe"
    raise ValueError(f"不明なNuSMV検査結果です: {property_line}")


def evaluate_case(case, root, nusmv, smv_dir):
    """1構成をSMVへ変換してNuSMVを実行し，期待判定と比較する．"""
    model_path = root / case["model"]
    chain = load_json(model_path)
    smv_path = smv_dir / f"{case['id'].lower()}.smv"
    smv_path.parent.mkdir(parents=True, exist_ok=True)
    smv_path.write_text(
        chain_to_nusmv.render_model(chain, str(model_path)), encoding="utf-8"
    )
    # shellを介さず引数配列でNuSMVを起動し，異常終了した場合はその場で停止する．
    completed = subprocess.run(
        [str(nusmv), str(smv_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    verdict = parse_verdict(completed.stdout)
    return {
        "id": case["id"],
        "expectedVerdict": case["expectedVerdict"],
        "actualVerdict": verdict,
        "correct": verdict == case["expectedVerdict"],
        "basis": case["basis"],
        "model": case["model"],
        "smv": str(smv_path.relative_to(root)),
    }


def summarize(results):
    """各構成の結果を混同行列と一致率へ集計する．"""
    # unsafeを陽性，safeを陰性として数える．
    true_positive = sum(
        result["expectedVerdict"] == "unsafe"
        and result["actualVerdict"] == "unsafe"
        for result in results
    )
    true_negative = sum(
        result["expectedVerdict"] == "safe"
        and result["actualVerdict"] == "safe"
        for result in results
    )
    false_positive = sum(
        result["expectedVerdict"] == "safe"
        and result["actualVerdict"] == "unsafe"
        for result in results
    )
    false_negative = sum(
        result["expectedVerdict"] == "unsafe"
        and result["actualVerdict"] == "safe"
        for result in results
    )
    correct = true_positive + true_negative
    total = len(results)
    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "confusionMatrix": {
            "truePositive": true_positive,
            "trueNegative": true_negative,
            "falsePositive": false_positive,
            "falseNegative": false_negative,
        },
    }


def write_json(path, value):
    """親directoryを作成し，読みやすい形式でJSONを書き出す．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        json.dump(value, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


def main():
    """評価対象一覧を読み，全構成のNuSMV検査結果をJSONへ保存する．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--nusmv", type=Path, required=True)
    parser.add_argument("--smv-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_json(args.input)
    results = [
        evaluate_case(case, args.root, args.nusmv, args.smv_dir)
        for case in manifest["cases"]
    ]
    output = {
        "schemaVersion": "0.1.0",
        "property": "未信頼な成果物を保持したまま権限へ到達しない．",
        "cases": results,
        "summary": summarize(results),
        "limitations": [
            "この評価集合は小規模であり，一般的な検出精度を示すものではない．",
            "shell処理における成果物利用と権限到達の意味付けには手動判断を含む．"
        ]
    }
    write_json(args.output, output)


if __name__ == "__main__":
    main()
