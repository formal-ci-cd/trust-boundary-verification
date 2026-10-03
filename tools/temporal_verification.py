#!/usr/bin/env python3
"""共有ファイルの検査・置換・利用の順序をNuSMVと独立BFSで比較する．"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import subprocess
import time

MODES = ("mutable", "snapshot", "revalidate")


def initial(count):
    # writer済み, readerのpc, sharedのtaint, localのtaint, verified, bad
    return (
        tuple(False for _ in range(count)),
        tuple(0 for _ in range(count)),
        tuple(False for _ in range(count)),
        tuple(False for _ in range(count)),
        tuple(False for _ in range(count)),
        False,
    )


def successors(state, mode):
    """各actorを一つ進める．NuSMV生成テキストを読まず独立に状態探索する．"""
    writers, pc, shared, local, verified, bad = state
    for i in range(len(pc)):
        if not writers[i]:
            w, s = list(writers), list(shared)
            w[i], s[i] = True, True
            yield f"writer[{i}]:replace", (tuple(w), pc, tuple(s), local, verified, bad)
        if pc[i] == 3:
            continue
        p, l, v = list(pc), list(local), list(verified)
        new_bad = bad
        if pc[i] == 0:
            l[i] = shared[i]
            p[i] = 1
            action = "restore"
        elif pc[i] == 1:
            v[i] = not (local[i] if mode == "snapshot" else shared[i])
            p[i] = 2 if v[i] else 3
            action = "verify"
        else:
            used = local[i] if mode == "snapshot" else shared[i]
            if verified[i] and (mode != "revalidate" or not shared[i]):
                new_bad = bad or used
            p[i] = 3
            action = "use"
        yield f"consumer[{i}]:{action}", (
            writers,
            tuple(p),
            shared,
            tuple(l),
            tuple(v),
            new_bad,
        )


def explore(count, mode):
    started = time.perf_counter()
    start = initial(count)
    queue = deque([start])
    parents = {start: None}
    bad_state = None
    # 完全探索し，反例1本だけで探索を打ち切らない．件数/規模を再現可能にする．
    while queue:
        state = queue.popleft()
        if state[-1] and bad_state is None:
            bad_state = state
        for action, nxt in successors(state, mode):
            if nxt not in parents:
                parents[nxt] = (state, action)
                queue.append(nxt)
    trace = []
    while bad_state is not None and parents[bad_state] is not None:
        prev, action = parents[bad_state]
        trace.append(
            {
                "action": action,
                "sharedTainted": list(bad_state[2]),
                "verified": list(bad_state[4]),
                "bad": bad_state[-1],
            }
        )
        bad_state = prev
    trace.reverse()
    return {
        "verdict": "unsafe" if trace else "safe",
        "reachableStates": len(parents),
        "seconds": time.perf_counter() - started,
        "counterexample": trace,
    }


def render(count, mode):
    if mode not in MODES or not 1 <= count <= 8:
        raise ValueError("mode must be supported and count must be 1..8")
    lines = [
        "-- Synthetic mutable shared-file model; not GitHub artifact replacement semantics",
        "MODULE main",
        "IVAR",
        f"  action : 0..{2*count-1};",
        "VAR",
        "  bad : boolean;",
    ]
    for i in range(count):
        lines += [
            f"  written_{i} : boolean;",
            f"  pc_{i} : 0..3;",
            f"  shared_{i} : boolean;",
            f"  local_{i} : boolean;",
            f"  verified_{i} : boolean;",
        ]
    lines += ["ASSIGN", "  init(bad) := FALSE;", "  next(bad) := case"]
    for i in range(count):
        used = f"local_{i}" if mode == "snapshot" else f"shared_{i}"
        guard = f" & !shared_{i}" if mode == "revalidate" else ""
        lines += [
            f"    action = {count+i} & pc_{i} = 2 & verified_{i}{guard} & {used} : TRUE;"
        ]
    lines += ["    TRUE : bad;", "  esac;"]
    for i in range(count):
        writer = f"action = {i} & !written_{i}"
        reader = f"action = {count+i}"
        checked = f"local_{i}" if mode == "snapshot" else f"shared_{i}"
        for name in ["written", "shared", "local", "verified"]:
            lines.append(f"  init({name}_{i}) := FALSE;")
        lines += [
            f"  init(pc_{i}) := 0;",
            f"  next(written_{i}) := written_{i} | ({writer});",
            f"  next(shared_{i}) := shared_{i} | ({writer});",
            f"  next(local_{i}) := case {reader} & pc_{i} = 0 : shared_{i}; TRUE : local_{i}; esac;",
            f"  next(verified_{i}) := case {reader} & pc_{i} = 1 : !{checked}; TRUE : verified_{i}; esac;",
            f"  next(pc_{i}) := case",
            f"    {reader} & pc_{i} = 0 : 1;",
            f"    {reader} & pc_{i} = 1 & !{checked} : 2;",
            f"    {reader} & pc_{i} = 1 : 3;",
            f"    {reader} & pc_{i} = 2 : 3;",
            f"    TRUE : pc_{i};",
            "  esac;",
        ]
    return "\n".join(lines + ["CTLSPEC AG !bad", ""])


def safe_replay(trace, mode):
    """抽象反例を無害なファイルの読み書き/ハッシュ比較へ具体化する．権限行使はしない．"""
    import tempfile

    clean = b"trusted-build\n"
    untrusted = b"untrusted-marker\n"
    expected = hashlib.sha256(clean).hexdigest()
    records = []
    snapshots = {}
    verified = {}
    reached = False
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for item in trace:
            actor, operation = item["action"].split(":")
            i = int(actor.split("[")[1].split("]")[0])
            path = root / f"object-{i}"
            if not path.exists():
                path.write_bytes(clean)
            if actor.startswith("writer"):
                path.write_bytes(untrusted)
            elif operation == "restore":
                snapshots[i] = path.read_bytes()
            elif operation == "verify":
                content = snapshots[i] if mode == "snapshot" else path.read_bytes()
                verified[i] = hashlib.sha256(content).hexdigest() == expected
            elif operation == "use":
                content = snapshots[i] if mode == "snapshot" else path.read_bytes()
                allowed = verified.get(i, False) and (
                    mode != "revalidate"
                    or hashlib.sha256(content).hexdigest() == expected
                )
                reached |= allowed and content == untrusted
            records.append({"action": item["action"], "dummyAuthorityReached": reached})
    return {
        "dummyAuthorityReached": reached,
        "events": records,
        "realAuthorityExercised": False,
    }


def run_nusmv(binary, path):
    started = time.perf_counter()
    completed = subprocess.run(
        [str(binary), str(path)], text=True, capture_output=True, timeout=120
    )
    output = completed.stdout + completed.stderr
    if completed.returncode or "-- specification" not in output:
        raise RuntimeError(output)
    if "is false" in output:
        verdict = "unsafe"
    elif "is true" in output:
        verdict = "safe"
    else:
        raise RuntimeError("NuSMV verdict missing")
    return {
        "verdict": verdict,
        "seconds": time.perf_counter() - started,
        "output": output,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nusmv", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-objects", type=int, default=3)
    args = p.parse_args()
    if not 1 <= args.max_objects <= 4:
        p.error("complete BFS benchmark is limited to 1..4 objects")
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for n in range(1, args.max_objects + 1):
        for mode in MODES:
            path = args.output / f"{mode}-{n}.smv"
            path.write_text(render(n, mode))
            bfs = explore(n, mode)
            smv = run_nusmv(args.nusmv, path)
            (args.output / f"{mode}-{n}.nusmv.txt").write_text(smv.pop("output"))
            if bfs["verdict"] != smv["verdict"]:
                raise RuntimeError(f"verdict mismatch {mode}/{n}")
            replay = safe_replay(bfs["counterexample"], mode)
            if replay["dummyAuthorityReached"] != (bfs["verdict"] == "unsafe"):
                raise RuntimeError("counterexample replay mismatch")
            results.append(
                {
                    "objects": n,
                    "mode": mode,
                    "bfs": bfs,
                    "nusmv": smv,
                    "safeReplay": replay,
                }
            )
    (args.output / "comparison.json").write_text(
        json.dumps(
            {
                "scope": "synthetic mutable-file interleavings; no YAML concurrency inference",
                "property": "AG !bad",
                "results": results,
                "conclusion": "BFS and NuSMV agree. Necessity or performance superiority of model checking is not established.",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
