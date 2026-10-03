#!/usr/bin/env python3
"""YAMLを直接読み，CodeQL抽出表と互換な静的構造を作る．実行はしない．"""
import argparse
import copy
import json
import re
from pathlib import Path
import yaml
import codeql_csv_to_model as common


class WorkflowLoader(yaml.SafeLoader):
    pass


# PyYAMLのYAML 1.1ではon/off/yes/noがboolになる．GitHubのonキーを保つ．
WorkflowLoader.yaml_implicit_resolvers = copy.deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for key, values in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[key] = [
        (tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:bool"
    ]
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(
                f"duplicate YAML key {key!r}, line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node)
    return result


WorkflowLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_workflow(path, root):
    source = path.read_text(encoding="utf-8")
    data = yaml.load(source, Loader=WorkflowLoader)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise ValueError(f"not a workflow: {path}")
    # ASTから元のrunブロックの位置も取得する．行を分割して再構成しない．
    ast = yaml.compose(source, Loader=WorkflowLoader)

    def member(node, key):
        return (
            next((v for k, v in node.value if k.value == key), None)
            if isinstance(node, yaml.MappingNode)
            else None
        )

    job_nodes = member(ast, "jobs")
    file = path.relative_to(root).as_posix()
    name = data.get("name", file)
    rows = []

    def row(kind, detail="-", key="-", job="-", step="-", line=1):
        rows.append(
            dict(
                filePath=file,
                workflowName=name,
                kind=kind,
                detail=text(detail),
                name=key,
                jobId=job,
                stepIndex=str(step),
                line=str(line),
            )
        )

    row("workflow", key=name)
    events = data.get("on", {})
    if isinstance(events, str):
        events = {events: None}
    elif isinstance(events, list):
        events = {event: None for event in events}
    if not isinstance(events, dict):
        raise ValueError(f"unsupported on: {file}")
    for event, props in events.items():
        # この分類はイベントの権限コンテキストであり，実際のwrite権限ではない．
        external = event in {
            "pull_request",
            "pull_request_target",
            "pull_request_review",
            "pull_request_review_comment",
            "issue_comment",
            "issues",
            "workflow_run",
        }
        privileged = event in {
            "pull_request_target",
            "issue_comment",
            "issues",
            "workflow_run",
        }
        row(
            "event",
            ("externally-triggerable" if external else "not-externally-triggerable")
            + ";"
            + ("privileged" if privileged else "not-privileged"),
            key=event,
        )
        if isinstance(props, dict):
            for prop in ["workflows", "types", "branches"]:
                for value in props.get(prop, []):
                    row("event-property", value, event + "." + prop)

    def permissions(value, job="-"):
        if isinstance(value, str):
            value = {
                "*": {"read-all": "read", "write-all": "write"}.get(value, "unknown")
            }
        for scope, access in (value or {}).items():
            row("permission", f"{scope}:{access}", job=job)

    permissions(data.get("permissions"))
    for jid, job in data["jobs"].items():
        labels = job.get("runs-on", [])
        if isinstance(labels, str):
            labels = [labels]
        if isinstance(labels, dict):
            labels = labels.get("labels", [])
        if isinstance(labels, str):
            labels = [labels]
        for label in labels or ["unknown"]:
            row("job", "runs-on=" + text(label), key=jid, job=jid)
        permissions(job.get("permissions"), jid)
        for i, step in enumerate(job.get("steps", [])):
            if "uses" in step:
                row("uses-step", step["uses"], step.get("id", "-"), jid, i)
                for key, value in step.get("with", {}).items():
                    row("uses-argument", value, key, jid, i)
            elif "run" in step:
                row("run-step", step["run"], step.get("id", "-"), jid, i)
    model = common.build_model(rows, path)
    model["source"]["type"] = "yamlLibrary"
    model["workflow"]["permissionsDeclared"] = "permissions" in data
    model["workflow"]["env"] = {k: text(v) for k, v in data.get("env", {}).items()}
    for job in model["workflow"]["jobs"]:
        original = data["jobs"][job["id"]]
        node = member(job_nodes, job["id"])
        steps_node = member(node, "steps")
        job.update(
            condition=text(original.get("if", "")),
            env={k: text(v) for k, v in original.get("env", {}).items()},
            needs=original.get("needs", []),
            permissionsDeclared="permissions" in original,
            uses=text(original.get("uses", "")),
            arguments={k: text(v) for k, v in original.get("with", {}).items()},
            environment=original.get("environment", None),
        )
        for step in job["steps"]:
            raw = original["steps"][step["index"]]
            step_node = steps_node.value[step["index"]]
            step.update(
                id=raw.get("id", ""),
                condition=text(raw.get("if", "")),
                env={k: text(v) for k, v in raw.get("env", {}).items()},
                line=step_node.start_mark.line + 1,
                workingDirectory=text(raw.get("working-directory", original.get("defaults", {}).get("run", {}).get("working-directory", "."))),
            )
            if step["type"] == "run":
                labels = job["runnerLabels"]
                default_shell = (
                    "unknown"
                    if any("${{" in label for label in labels)
                    else "pwsh" if all(label.startswith("windows") for label in labels)
                    else "unknown" if any(label.startswith("windows") for label in labels)
                    else "bash"
                )
                step["command"] = raw["run"]
                step["commandOrderKnown"] = True
                step["commands"] = [
                    {
                        "text": raw["run"],
                        "line": member(step_node, "run").start_mark.line + 1,
                    }
                ]
                step["shell"] = raw.get(
                    "shell",
                    original.get("defaults", {})
                    .get("run", {})
                    .get(
                        "shell",
                        data.get("defaults", {}).get("run", {}).get("shell", default_shell),
                    ),
                )
    return model


def load_models(root):
    # ファイル名をキーにするため，同じ表示名のworkflowを上書きしない．
    paths = sorted(
        set((root / ".github/workflows").glob("*.yml"))
        | set((root / ".github/workflows").glob("*.yaml"))
    )
    return {p.relative_to(root).as_posix(): load_workflow(p, root) for p in paths}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    models = load_models(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(models, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
