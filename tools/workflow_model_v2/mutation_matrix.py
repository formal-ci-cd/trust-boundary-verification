"""Generate isolated YAML-copy mutations for the v2 container experiment."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = Path("/out/mutations")
sys.path.insert(0, str(ROOT / "tools"))
from workflow_model_v2.builder import compile_pair_v2  # noqa: E402


CASES = {
    "A1": ("artifact-a1-pr-producer.yml", "artifact-a1-unsafe-consumer.yml"),
    "A2": ("artifact-a1-pr-producer.yml", "artifact-a2-safe-consumer.yml"),
    "A5": ("artifact-a5-attest-producer.yml", "artifact-a5-attest-consumer.yml"),
}


def _step_bounds(text: str, selector: str) -> tuple[list[str], int, int]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^\s*-\s+name:\s*", line) and selector in line), None)
    if start is None:
        raise ValueError(f"step selector not found: {selector}")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^\s*-\s+", lines[i]) and len(lines[i]) - len(lines[i].lstrip()) == indent:
            end = i
            break
    return lines, start, end


def _insert_if_false(text: str, selector: str) -> str:
    lines, start, _ = _step_bounds(text, selector)
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2
    lines.insert(start + 1, " " * indent + "if: false")
    return "\n".join(lines) + "\n"


def _replace_step_line(text: str, selector: str, old: str, new: str) -> str:
    lines, start, end = _step_bounds(text, selector)
    found = [i for i in range(start, end) if old in lines[i]]
    if len(found) != 1:
        raise ValueError(f"expected one {old!r} in step {selector!r}, found {len(found)}")
    lines[found[0]] = lines[found[0]].replace(old, new, 1)
    return "\n".join(lines) + "\n"


def _insert_shell_line(text: str, selector: str, needle: str, insertion: str) -> str:
    lines, start, end = _step_bounds(text, selector)
    found = [i for i in range(start, end) if needle in lines[i]]
    if len(found) != 1:
        raise ValueError(f"expected one shell line {needle!r}, found {len(found)}")
    line = lines[found[0]]
    indent = len(line) - len(line.lstrip())
    lines.insert(found[0] + 1, " " * indent + insertion)
    return "\n".join(lines) + "\n"


def _append_step(text: str, name: str, run: str) -> str:
    return text.rstrip() + f"\n\n      - name: {name}\n        run: {run}\n"


def _mutate(slug: str, pair: str, mutation: str):
    producer_name, consumer_name = CASES[pair]
    producer = (WORKFLOWS / producer_name).read_text()
    consumer = (WORKFLOWS / consumer_name).read_text()
    constraint = None
    model_corruption = False

    if mutation == "download-disabled":
        consumer = _insert_if_false(consumer, "Download artifact")
    elif mutation == "use-disabled":
        consumer = _insert_if_false(consumer, "Use artifact for a dummy publish decision")
    elif mutation == "sink-disabled":
        consumer = _insert_if_false(consumer, "Simulate privileged repository update")
    elif mutation == "upload-name-mismatch":
        producer = _replace_step_line(producer, "Upload PR-controlled artifact",
            "name: trust-boundary-build-output", "name: different-artifact-name")
    elif mutation == "workflow-run-mismatch":
        consumer = consumer.replace('workflows: ["GHA-A Producer - untrusted PR artifact"]',
                                    'workflows: ["Different producer"]', 1)
    elif mutation == "run-id-mismatch":
        consumer = _replace_step_line(consumer, "Download artifact",
            "${{ github.event.workflow_run.id }}", "${{ github.run_id }}")
    elif mutation == "upload-failure":
        constraint = "upload_outcome=failure"
    elif mutation == "download-failure":
        constraint = "download_outcome=failure"
    elif mutation == "if-false-upload":
        producer = _insert_if_false(producer, "Upload PR-controlled artifact")
    elif mutation == "same-output-reference-twice":
        consumer = consumer.replace(
            "if: steps.verify-artifact.outputs.verified == 'true'",
            "if: steps.verify-artifact.outputs.verified == 'true' && steps.verify-artifact.outputs.verified == 'true'", 1)
    elif mutation == "contradictory-same-output":
        consumer = consumer.replace(
            "if: steps.verify-artifact.outputs.verified == 'true'",
            "if: steps.verify-artifact.outputs.verified == 'true' && steps.verify-artifact.outputs.verified != 'true'", 1)
    elif mutation == "integrity-check-disabled":
        consumer = _insert_if_false(consumer, "Verify artifact before use")
    elif mutation == "digest-success":
        constraint = "trusted=true;payload=payload_publish_false"
    elif mutation == "digest-mismatch":
        constraint = "trusted=true;payload=payload_publish_true"
    elif mutation == "a5-invalid-metadata":
        constraint = "metadata=invalid"
    elif mutation == "a5-output-reference-changed":
        consumer = _replace_step_line(consumer, "Simulate privileged repository update",
            "${{ steps.artifact-metadata.outputs.target_branch }}", "refs/heads/main")
    elif mutation == "unrelated-fixed-marker":
        consumer = _insert_if_false(consumer, "Simulate privileged repository update")
        consumer = _append_step(consumer, "Unrelated fixed marker",
                                "echo 'dummy_repository_update_reached=true'")
    elif mutation == "unsupported-shell":
        consumer = _insert_shell_line(consumer, "Use artifact for a dummy publish decision",
            'request=$(tr -d', 'eval "$request"')
    elif mutation == "unregistered-action-ref":
        producer = _replace_step_line(producer, "Upload PR-controlled artifact",
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/upload-artifact@unregistered-ref")
    elif mutation == "permission-missing":
        consumer = consumer.replace("  actions: read\n", "", 1)
    elif mutation == "nusmv-invalid-input":
        model_corruption = True
    else:
        raise ValueError(f"unknown mutation: {mutation}")

    dest = OUT / slug
    inputs = dest / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    producer_path, consumer_path = inputs / producer_name, inputs / consumer_name
    producer_path.write_text(producer)
    consumer_path.write_text(consumer)
    smv, manifest = compile_pair_v2(producer_path, consumer_path)

    if constraint:
        transition_restriction = None
        if constraint in {"upload_outcome=failure", "download_outcome=failure"}:
            object_match = re.search(r"\bobject_([0-9a-f]+)_state\s*:", smv)
            download_match = re.search(r"\bdownload_([0-9a-f]+)_state\s*:", smv)
            token = object_match.group(1) if constraint.startswith("upload") and object_match else None
            token = download_match.group(1) if constraint.startswith("download") and download_match else token
            if not token:
                raise ValueError(f"could not locate action token for {constraint}")
            transition_restriction = "outcome_" + token + " = failure"
            invar = None
        elif constraint.startswith("trusted="):
            _, payload = constraint.split(";", 1)
            payload = payload.split("=", 1)[1]
            invar = "consumer_workflow_trusted = TRUE & producer_payload = " + payload
        else:
            invar = "producer_metadata_branch = branch_invalid & producer_metadata_sha = sha_invalid"
        if transition_restriction:
            marker = "CTLSPEC"
            index = smv.find(marker)
            if index < 0:
                raise ValueError("generated model has no CTLSPEC section for an input restriction")
            smv = smv[:index] + "TRANS " + transition_restriction + "\n" + smv[index:]
            manifest["experimentInputConstraint"] = "TRANS " + transition_restriction
        else:
            match = re.search(r"(?ms)^INVAR\n(.*?)\nASSIGN$", smv)
            if not match:
                raise ValueError("generated model has no INVAR section for a scenario input restriction")
            existing_invar = match.group(1).strip().removesuffix(";").strip()
            smv = (smv[:match.start()] + "INVAR\n  (" + invar + ") & (" + existing_invar +
                   ");\nASSIGN" + smv[match.end():])
            manifest["experimentInputConstraint"] = invar

    if model_corruption:
        smv += "\nTHIS_IS_INTENTIONALLY_INVALID_MODEL_SYNTAX\n"
    model_path = dest / (slug + ".smv")
    model_path.write_text(smv)
    (dest / (slug + ".map.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    diff = "".join(difflib.unified_diff(
        (WORKFLOWS / producer_name).read_text().splitlines(keepends=True),
        producer.splitlines(keepends=True), fromfile="original/" + producer_name,
        tofile="mutated/" + producer_name))
    diff += "".join(difflib.unified_diff(
        (WORKFLOWS / consumer_name).read_text().splitlines(keepends=True),
        consumer.splitlines(keepends=True), fromfile="original/" + consumer_name,
        tofile="mutated/" + consumer_name))
    (dest / "input.diff").write_text(diff or "# No YAML edit; model input restriction or parser-failure control only.\n")
    result = {"case": pair, "mutation": mutation, "inputDiff": str((dest / "input.diff").relative_to(OUT)),
              "model": str(model_path.relative_to(OUT)), "modelCorruptionControl": model_corruption,
              "experimentInputConstraint": manifest.get("experimentInputConstraint"),
              "sourceHashes": [item["sha256"] for item in manifest.get("sources", [])],
              "diagnostics": manifest.get("diagnostics", [])}
    (dest / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
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
        ("nusmv-invalid-input", "A1", "nusmv-invalid-input"),
    ]
    results = [_mutate(slug, case, mutation) for slug, case, mutation in scenarios]
    (OUT / "matrix.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    for result in results:
        print(result["case"], result["mutation"], result["model"],
              "diagnostics=" + ",".join(d["code"] for d in result["diagnostics"]))


if __name__ == "__main__":
    main()
