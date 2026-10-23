# 元のworkflowを変更せず，専用出力先のコピーへ対照実験用の変更を加える．
# 変更後も共通の変換器を呼ぶ．
# 動作条件はscenarioで指定し，生成SMVへの文字列注入は行わない．
"""元入力を残し，対照実験用のYAMLコピーとscenarioを生成する．

入力は登録caseとmutationの名前，出力は専用directoryの入力・SMV・対応表・差分・一覧である．
stepの条件，name/run-id等を変更したコピーを，共通のcompile_workflowsへ渡す．
成否・内容・信頼条件の固定はscenarioで行い，通常のモデルへSMV変数を文字列注入しない．
構文失敗の検査用ケースだけは，生成SMVへ意図した不正構文を追加する．
mainは既定の21対照を作り，評価スクリプトはOUTを専用出力先へ変更して呼ぶ．
"""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = Path("/out/mutations")
sys.path.insert(0, str(ROOT / "tools"))
from workflow_model_v2.compiler import compile_workflows
# noqa: E402
from workflow_model_v2.scenario import default_scenario
from workflow_model_v2.workflow_loader import WorkflowLoader
import yaml
CASES = {
    "A1": ("artifact-a1-pr-producer.yml", "artifact-a1-unsafe-consumer.yml"),
    "A2": ("artifact-a1-pr-producer.yml", "artifact-a2-safe-consumer.yml"),
    "A5": ("artifact-a5-attest-producer.yml", "artifact-a5-attest-consumer.yml")
}

def _step_bounds(text: str, selector: str) -> tuple[list[str], int, int]:
    """nameにselectorを含むstepの行範囲を探す．"""
    # 入力のコピーを行単位で扱い，元ファイル自体には変更を加えない．
    lines = text.splitlines()
    # name行にselectorを含むstepを最初の1個だけ探し，その開始位置を得る．
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s*-\s+name:\s*", line) and selector in line),
        None
    )
    # 対象stepを見つけられなければ，別の場所を推測して変更しない．
    if start is None:
        raise ValueError(f'step selector not found: {selector}')
    # step行の字下げ幅を測り，同じ深さの次stepを境界として探す．
    indent = len(lines[start]) - len(lines[start].lstrip())
    # 次stepがない場合はファイル末尾までを，選択stepの範囲として使う．
    end = len(lines)
    # 選択stepの次の行から走査し，同じ深さの箇条書き開始を探す．
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*-\s+", lines[i]) and len(lines[i]) - len(lines[i].lstrip()) == indent:
            end = i
            break
    return (lines, start, end)

def _insert_if_false(text: str, selector: str) -> str:
    """選択stepに同じ深さのif:falseを挿入したコピーを作る．"""
    lines, start, _ = _step_bounds(text, selector)
    # step行の字下げ幅を測り，同じ深さの次stepを境界として探す．
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    lines.insert(start + 1, " " * indent + "if: false")
    return "\n".join(lines) + "\n"

def _replace_step_line(text: str, selector: str, old: str, new: str) -> str:
    """選択step内のただ1行だけを置換する．"""
    lines, start, end = _step_bounds(text, selector)
    # 選択step内だけで置換対象の行番号を集め，別stepの同じ文字列を変更しない．
    found = [i for i in range(start, end) if old in lines[i]]
    # 対象が0件・複数件なら意図した一点変更にならないため，エラーにする．
    if len(found) != 1:
        raise ValueError(f'expected one {old!r} in step {selector!r}, found {len(found)}')
    lines[found[0]] = lines[found[0]].replace(old, new, 1)
    return "\n".join(lines) + "\n"

def _insert_shell_line(text: str, selector: str, needle: str, insertion: str) -> str:
    """選択stepの所定run行の直後へ1行を追加する．"""
    lines, start, end = _step_bounds(text, selector)
    # 選択step内だけで置換対象の行番号を集め，別stepの同じ文字列を変更しない．
    found = [i for i in range(start, end) if needle in lines[i]]
    # 対象が0件・複数件なら意図した一点変更にならないため，エラーにする．
    if len(found) != 1:
        raise ValueError(f'expected one shell line {needle!r}, found {len(found)}')
    line = lines[found[0]]
    indent = len(line) - len(line.lstrip())
    lines.insert(found[0] + 1, " " * indent + insertion)
    return "\n".join(lines) + "\n"

def _append_step(text: str, name: str, run: str) -> str:
    """所定indentのstep文字列を末尾へ追加する．"""
    return text.rstrip() + f'\n\n      - name: {name}\n        run: {run}\n'

# 対照ケースごとに入力コピーの一点を変え，変更内容と対応するscenarioを保存する．
def _mutate(slug: str, pair: str, mutation: str):
    """一つの対照について入力コピー，scenario，モデル，差分を保存する．"""
    producer_name, consumer_name = CASES[pair]
    producer = (WORKFLOWS / producer_name).read_text()
    consumer = (WORKFLOWS / consumer_name).read_text()
    # 入力内容以外に固定するscenario条件は，変更種別が必要とする場合だけ設定する．
    constraint = None
    # 通常の対照ではSMVを直接変更せず，構文不正の専用対照だけを後で有効にする．
    model_corruption = False
    if mutation == "download-disabled":
        consumer = _insert_if_false(consumer, "Download artifact")
    elif mutation == "use-disabled":
        consumer = _insert_if_false(consumer, "Use artifact for a dummy publish decision")
    elif mutation == "sink-disabled":
        consumer = _insert_if_false(consumer, "Simulate privileged repository update")
    elif mutation == "upload-name-mismatch":
        producer = _replace_step_line(
            producer,
            "Upload PR-controlled artifact",
            "name: trust-boundary-build-output",
            "name: different-artifact-name"
        )
    elif mutation == "workflow-run-mismatch":
        consumer = consumer.replace(
            'workflows: ["GHA-A Producer - untrusted PR artifact"]',
            'workflows: ["Different producer"]',
            1
        )
    elif mutation == "run-id-mismatch":
        consumer = _replace_step_line(
            consumer,
            "Download artifact",
            "${{ github.event.workflow_run.id }}",
            "${{ github.run_id }}"
        )
    elif mutation == "upload-failure":
        constraint = "upload_outcome=failure"
    elif mutation == "download-failure":
        constraint = "download_outcome=failure"
    elif mutation == "if-false-upload":
        producer = _insert_if_false(producer, "Upload PR-controlled artifact")
    elif mutation == "same-output-reference-twice":
        consumer = consumer.replace(
            "if: steps.verify-artifact.outputs.verified == 'true'",
            "if: steps.verify-artifact.outputs.verified == 'true' && steps.verify-artifact.outputs.verified == 'true'",
            1
        )
    elif mutation == "contradictory-same-output":
        consumer = consumer.replace(
            "if: steps.verify-artifact.outputs.verified == 'true'",
            "if: steps.verify-artifact.outputs.verified == 'true' && steps.verify-artifact.outputs.verified != 'true'",
            1
        )
    elif mutation == "integrity-check-disabled":
        consumer = _insert_if_false(consumer, "Verify artifact before use")
    elif mutation == "digest-success":
        constraint = "trusted=true;payload=payload_publish_false"
    elif mutation == "digest-mismatch":
        constraint = "trusted=true;payload=payload_publish_true"
    elif mutation == "a5-invalid-metadata":
        constraint = "metadata=invalid"
    elif mutation == "a5-output-reference-changed":
        consumer = _replace_step_line(
            consumer,
            "Simulate privileged repository update",
            "${{ steps.artifact-metadata.outputs.target_branch }}",
            "refs/heads/main"
        )
    elif mutation == "unrelated-fixed-marker":
        consumer = _insert_if_false(consumer, "Simulate privileged repository update")
        consumer = _append_step(
            consumer,
            "Unrelated fixed marker",
            "echo 'dummy_repository_update_reached=true'"
        )
    elif mutation == "unsupported-shell":
        consumer = _insert_shell_line(
            consumer,
            "Use artifact for a dummy publish decision",
            'request=$(tr -d',
            'eval "$request"'
        )
    elif mutation == "unregistered-action-ref":
        producer = _replace_step_line(
            producer,
            "Upload PR-controlled artifact",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/upload-artifact@unregistered-ref"
        )
    elif mutation == "permission-missing":
        consumer = consumer.replace("  actions: read\n", "", 1)
    elif mutation == "nusmv-invalid-input":
        model_corruption = True
    else:
        raise ValueError(f'unknown mutation: {mutation}')
    dest = OUT / slug
    inputs = dest / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    producer_path, consumer_path = (inputs / producer_name, inputs / consumer_name)
    # 元workflowではなく，専用出力先に置いたProducerの入力コピーを保存する．
    producer_path.write_text(producer)
    # Consumerも専用出力先へ保存し，元の研究用YAMLを変更しない．
    consumer_path.write_text(consumer)
    # 変更後の入力コピーから有限の既定contextを作り，追加の成否・内容制約を加える．
    scenario = default_scenario([producer_path, consumer_path])
    if constraint in {'upload_outcome=failure', 'download_outcome=failure'}:
        wi = 0 if constraint.startswith('upload') else 1
        raw = yaml.load([producer, consumer][wi], Loader=WorkflowLoader)
        action = 'upload-artifact' if wi == 0 else 'download-artifact'
        scenario['stepOutcomes'] = {}
        for name, job in raw['jobs'].items():
            for index, step in enumerate(job['steps']):
                if action in str(step.get('uses', '')):
                    key = f"run{wi}:{name}:{step.get('id', f'index{index}')}"
                    # 選択したupload/downloadだけを失敗へ固定し，共通backendの通常の成否規則を使う．
                    scenario['stepOutcomes'][key] = False
    elif constraint and constraint.startswith('trusted='):
        scenario['runs'][1]['workflowTrusted'] = True
        value = 'publish=false\n' if 'payload_publish_false' in constraint else 'publish=true\n'
        for path, values in scenario['runs'][0]['checkoutValues'].items():
            if value in values:
                scenario['runs'][0]['checkoutValues'][path] = [value]
    elif constraint == 'metadata=invalid':
        for path, values in scenario['runs'][0]['checkoutValues'].items():
            invalid = [value for value in values if value.startswith('invalid')]
            if invalid:
                scenario['runs'][0]['checkoutValues'][path] = invalid
    # 対照ごとに別backendを作らず，通常と同じ変換器へ入力コピーとscenarioを渡す．
    smv, manifest = compile_workflows([producer_path, consumer_path], scenario)
    manifest['experimentInputConstraint'] = constraint
    (dest / 'scenario.json').write_text(json.dumps(scenario, indent=2) + '\n')
    # NuSMVの不正入力検出を確かめる対照例に限り，意図した構文エラーを追加する．
    if model_corruption:
        smv += "\nTHIS_IS_INTENTIONALLY_INVALID_MODEL_SYNTAX\n"
    model_path = dest / (slug + ".smv")
    # 対照のSMVを専用directoryへ保存し，通常modelや過去の実験結果を上書きしない．
    model_path.write_text(smv)
    # 意図的な不正構文を含む対照も，実際にNuSMVへ渡す本文のhashを記録する．
    manifest['modelSha256'] = hashlib.sha256(smv.encode('utf-8')).hexdigest()
    (dest / (slug + ".map.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    diff = "".join(difflib.unified_diff(
        (WORKFLOWS / producer_name).read_text().splitlines(keepends=True),
        producer.splitlines(keepends=True),
        fromfile="original/" + producer_name,
        tofile="mutated/" + producer_name
    ))
    diff += "".join(difflib.unified_diff(
        (WORKFLOWS / consumer_name).read_text().splitlines(keepends=True),
        consumer.splitlines(keepends=True),
        fromfile="original/" + consumer_name,
        tofile="mutated/" + consumer_name
    ))
    (dest / "input.diff").write_text(diff or "# No YAML edit; model input restriction or parser-failure control only.\n")
    result = {
        "case": pair,
        "mutation": mutation,
        "inputDiff": str((dest / "input.diff").relative_to(OUT)),
        "model": str(model_path.relative_to(OUT)),
        "modelCorruptionControl": model_corruption,
        "experimentInputConstraint": manifest.get("experimentInputConstraint"),
        "sourceHashes": [item["sha256"] for item in manifest.get("sources", [])],
        "diagnostics": manifest.get("diagnostics", [])
    }
    (dest / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result

def main():
    """登録した21対照を生成し，一覧JSONを保存する．"""
    scenarios = [
        ("download-disabled", "A1", "download-disabled"),
        ("use-disabled", "A1", "use-disabled"),
        ("sink-disabled", "A5", "sink-disabled"),
        ("upload-name-mismatch", "A1", "upload-name-mismatch"),
        ("workflow-run-mismatch", "A1", "workflow-run-mismatch"),
        ("run-id-mismatch", "A1", "run-id-mismatch"),
        ("upload-failure", "A1", "upload-failure"),
        ("download-failure", "A1", "download-failure"),
        ("if-false-upload", "A1", "if-false-upload"),
        ("same-output-reference-twice", "A2", "same-output-reference-twice"),
        ("contradictory-same-output", "A2", "contradictory-same-output"),
        ("integrity-check-disabled", "A2", "integrity-check-disabled"),
        ("digest-success", "A2", "digest-success"),
        ("digest-mismatch", "A2", "digest-mismatch"),
        ("a5-invalid-metadata", "A5", "a5-invalid-metadata"),
        ("a5-output-reference-changed", "A5", "a5-output-reference-changed"),
        ("unrelated-fixed-marker", "A5", "unrelated-fixed-marker"),
        ("unsupported-shell", "A1", "unsupported-shell"),
        ("unregistered-action-ref", "A1", "unregistered-action-ref"),
        ("permission-missing", "A1", "permission-missing"),
        ("nusmv-invalid-input", "A1", "nusmv-invalid-input")
    ]
    results = [_mutate(slug, case, mutation) for slug, case, mutation in scenarios]
    (OUT / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    for result in results:
        print(
            result["case"],
            result["mutation"],
            result["model"],
            "diagnostics=" + ",".join((d["code"] for d in result["diagnostics"]))
        )
if __name__ == "__main__":
    main()
