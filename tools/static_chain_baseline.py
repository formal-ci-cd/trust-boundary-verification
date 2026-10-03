#!/usr/bin/env python3
"""既存の固定事実モデルと同じ安全性条件を直接判定する比較baseline．"""
import argparse
import itertools
import json
from pathlib import Path
import chain_to_nusmv


def check(chain):
    facts = chain_to_nusmv.extract_facts(chain)
    unknown = [name for name, value in facts.items() if value == "unknown"]
    for values in itertools.product([False, True], repeat=len(unknown)):
        f = facts | dict(zip(unknown, values))
        if (
            (f["write_succeeded"] and not f["write_authorized"])
            or (f["read_succeeded"] and not f["write_succeeded"])
            or (f["integrity_check_passed"] and not f["integrity_check_present"])
        ):
            continue
        # 検査失敗では停止し，成功ではtaintを消すため，未検査の到達だけが違反する．
        if (
            all(
                f[name]
                for name in [
                    "producer_untrusted",
                    "write_intent",
                    "write_authorized",
                    "write_succeeded",
                    "same_object",
                    "read_succeeded",
                    "consumer_uses_object",
                    "privileged_consumer",
                    "has_authority",
                ]
            )
            and not f["integrity_check_present"]
        ):
            return {
                "verdict": "unsafe",
                "witness": f,
                "scope": "same frozen-fact abstraction; not observed exploitation",
            }
    return {
        "verdict": "safe",
        "witness": None,
        "scope": "same frozen-fact abstraction; not a repository safety verdict",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("chain", type=Path)
    args = p.parse_args()
    print(json.dumps(check(json.loads(args.chain.read_text())), indent=2))


if __name__ == "__main__":
    main()
