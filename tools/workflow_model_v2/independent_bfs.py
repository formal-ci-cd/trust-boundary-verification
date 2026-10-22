"""Independent bounded BFS for one-job Artifact download/use semantics."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
from . import frontend as base  # noqa: E402
from workflow_shell_subset import UnsupportedShell, parse_shell  # noqa: E402


class BFSUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class State:
    producer_pc: int
    consumer_pc: int
    uploaded: bool
    download: str
    dependent_use: bool


def _action(step: base.Step) -> str:
    return step.uses.rsplit("@", 1)[0] if step.uses else ""


def _eval(expr: base.Expr | None, env: dict[str, Any]) -> Any:
    if expr is None:
        return True
    if expr.kind in {"bool", "string"}:
        return expr.value
    if expr.kind == "ref":
        if expr.value not in env:
            raise BFSUnsupported(f"unknown expression reference {expr.value!r}")
        return env[expr.value]
    if expr.kind == "call":
        if expr.value == "success":
            return True
        raise BFSUnsupported(f"unknown status function {expr.value!r}")
    if expr.kind == "not":
        return not bool(_eval(expr.left, env))
    if expr.kind in {"&&", "||"}:
        left, right = bool(_eval(expr.left, env)), bool(_eval(expr.right, env))
        return left and right if expr.kind == "&&" else left or right
    if expr.kind in {"==", "!="}:
        left, right = _eval(expr.left, env), _eval(expr.right, env)
        return left == right if expr.kind == "==" else left != right
    raise BFSUnsupported(f"unsupported expression node {expr.kind!r}")


def _permission(wf: base.Workflow, job: base.Job, scope: str) -> str | None:
    source = job.permissions if job.permissions_declared else wf.permissions
    if not (job.permissions_declared or wf.permissions_declared):
        return None
    source = source or {}
    return source.get("*", source.get(scope, "none"))


def _under(download_path: str, file_path: str) -> bool:
    root = download_path.rstrip("/")
    return file_path == root or file_path.startswith(root + "/")


def _workflow_run_matches(producer: base.Workflow, consumer: base.Workflow) -> bool:
    cfg = consumer.events.get("workflow_run")
    if not isinstance(cfg, dict):
        return False
    names = cfg.get("workflows", [producer.name])
    types = cfg.get("types", ["requested", "in_progress", "completed"])
    names = [names] if isinstance(names, str) else names
    types = [types] if isinstance(types, str) else types
    if producer.name not in names or "completed" not in types:
        return False
    branches = cfg.get("branches", [])
    branches = [branches] if isinstance(branches, str) else branches
    if any(any(c in str(v) for c in "*?![]") for v in branches):
        raise BFSUnsupported("workflow_run branch glob is outside this BFS subset")
    return True


def explore(producer_path: Path, consumer_path: Path) -> dict[str, Any]:
    producer, consumer = base.load_workflow(producer_path), base.load_workflow(consumer_path)
    if len(producer.jobs) != 1 or len(consumer.jobs) != 1:
        raise BFSUnsupported("requires exactly one job per workflow")
    pj, cj = producer.jobs[0], consumer.jobs[0]
    if pj.needs or cj.needs:
        raise BFSUnsupported("job needs are outside this BFS subset")
    event = "pull_request" if "pull_request" in producer.events else "push"
    if event not in producer.events or not _workflow_run_matches(producer, consumer):
        raise BFSUnsupported("producer event or completed workflow_run does not match")
    event_cfg = producer.events[event] or {}
    if not isinstance(event_cfg, dict):
        raise BFSUnsupported("producer event config is outside this BFS subset")
    for key in ("branches", "branches-ignore"):
        values = event_cfg.get(key, [])
        values = [values] if isinstance(values, str) else values
        if any(any(c in str(v) for c in "*?![]") for v in values):
            raise BFSUnsupported("producer branch glob is outside this BFS subset")
    uploads = [s for s in pj.steps if _action(s) == "actions/upload-artifact"]
    downloads = [s for s in cj.steps if _action(s) == "actions/download-artifact"]
    if len(uploads) != 1 or len(downloads) != 1:
        raise BFSUnsupported("requires exactly one upload and one download")
    upload, download = uploads[0], downloads[0]
    upload_name, download_name = upload.inputs.get("name"), download.inputs.get("name")
    download_path = download.inputs.get("path", base.GH_EXPR_PREFIX + " github.workspace }}")
    run_id, token = download.inputs.get("run-id"), download.inputs.get("github-token")
    if not all(isinstance(x, str) for x in (upload_name, download_name, download_path, run_id, token)):
        raise BFSUnsupported("dynamic artifact input is outside this BFS subset")
    if run_id != base.GH_EXPR_PREFIX + " github.event.workflow_run.id }}" or token not in {
        base.GH_EXPR_PREFIX + " secrets.GITHUB_TOKEN }}",
        base.GH_EXPR_PREFIX + " github.token }}",
    }:
        raise BFSUnsupported("download run-id/token is outside the bounded contract")
    action_permission = _permission(consumer, cj, "actions")
    if action_permission not in {"read", "write"}:
        raise BFSUnsupported("effective actions permission is not explicitly read/write")

    programs = {}
    for wf, job in ((producer, pj), (consumer, cj)):
        for step in job.steps:
            if step.run is None:
                continue
            try:
                line = step.run_span.line + 1 if step.run_span else step.span.line
                programs[(wf.path, step.index)] = parse_shell(step.run, line)
            except UnsupportedShell as exc:
                raise BFSUnsupported(f"unsupported complete Shell at {wf.path}:{exc.span.line}") from exc

    # This bounded scenario fixes path-filter=true, a PR source, successful
    # outcomes, and a completed successful workflow_run.
    env = {
        "github.event_name": "workflow_run",
        "github.event.workflow_run.event": event,
        "github.event.workflow_run.conclusion": "success",
        "github.event.workflow_run.name": producer.name,
    }
    if not _eval(pj.condition, env) or not _eval(cj.condition, env):
        raise BFSUnsupported("job condition is false under the selected event")

    p_count, c_count = len(pj.steps), len(cj.steps)
    initial = State(-2, -2, False, "not_attempted", False)
    reached, queue = {initial}, [initial]
    edges = 0
    while queue:
        state = queue.pop(0)
        successors = []
        ppc, cpc = state.producer_pc, state.consumer_pc
        # Each successor represents one nondeterministic scheduler choice.
        if ppc == -2:
            successors.append(State(-1, cpc, state.uploaded, state.download, state.dependent_use))
        elif ppc == -1:
            successors.append(State(0, cpc, state.uploaded, state.download, state.dependent_use))
        elif ppc < p_count:
            step = pj.steps[ppc]
            enabled = bool(_eval(step.condition, env))
            uploaded = state.uploaded or (enabled and _action(step) == "actions/upload-artifact"
                                          and upload_name == download_name)
            successors.append(State(ppc + 1, cpc, uploaded, state.download, state.dependent_use))

        if cpc == -2:
            if ppc == p_count:
                successors.append(State(ppc, -1, state.uploaded, state.download, state.dependent_use))
            else:
                successors.append(state)
        elif cpc == -1:
            successors.append(State(ppc, 0, state.uploaded, state.download, state.dependent_use))
        elif cpc < c_count:
            step = cj.steps[cpc]
            enabled = bool(_eval(step.condition, env))
            download_state, used = state.download, state.dependent_use
            action = _action(step)
            if enabled and action == "actions/download-artifact":
                download_state = "succeeded" if state.uploaded and upload_name == download_name else "failed"
            elif enabled and step.run is not None:
                program = programs[(consumer.path, step.index)]
                for op in program.artifact_reads:
                    if any(
                        (file_path := step.env.get(var)) is not None
                        and _under(str(download_path), str(file_path))
                        and state.download == "succeeded"
                        for var in op.reads
                    ):
                        used = True
            successors.append(State(ppc, cpc + 1, state.uploaded, download_state, used))

        for successor in successors:
            edges += 1
            if successor not in reached:
                reached.add(successor)
                queue.append(successor)

    return {
        "semantics": "yaml-operational-bfs-download-use-subset-v1",
        "sources": [
            {"path": producer.path, "sha256": producer.sha256},
            {"path": consumer.path, "sha256": consumer.sha256},
        ],
        "scenario": {
            "producerEvent": event,
            "producerPathFilter": "true (fixed unknown input)",
            "stepOutcomes": "all success",
            "workflowRunActivity": "completed",
            "actionPermission": action_permission,
        },
        "reachableProjectedStateCount": len(reached),
        "exploredEdges": edges,
        "downloadReachable": any(s.download == "succeeded" for s in reached),
        "artifactDependentUseReachable": any(s.dependent_use for s in reached),
        "useRequiresPriorSuccessfulDownload": all(
            not s.dependent_use or s.download == "succeeded" for s in reached
        ),
        "limits": [
            "one job per workflow and one upload/download",
            "only Artifact-file reads count as dependent use",
            "integrity, sink, actual run ID/artifact ID, and runner behavior are excluded",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("producer", type=Path)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = explore(args.producer, args.consumer)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
