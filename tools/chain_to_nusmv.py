#!/usr/bin/env python3
"""複数run間の信頼経路をNuSMVモデルへ変換する．"""

import argparse
import json
from pathlib import Path


UNKNOWN = "unknown"
FACT_MAPPING = {
    "producer_untrusted": "producerUntrusted",
    "write_intent": "writeIntent",
    "write_authorized": "writeAuthorized",
    "write_succeeded": "writeSucceeded",
    "same_object": "sameObject",
    "read_succeeded": "readSucceeded",
    "consumer_uses_object": "consumerUsesObject",
    "integrity_check_present": "integrityCheckPresent",
    "integrity_check_passed": "integrityCheckPassed",
    "privileged_consumer": "privilegedConsumer",
    "has_authority": "hasAuthority",
}


def normalize_fact(value):
    if value == "true":
        return True
    if value == "false":
        return False
    if value == UNKNOWN:
        return UNKNOWN
    raise ValueError(f"不明な事実値です: {value}")


def extract_facts(chain):
    return {
        smv_name: normalize_fact(chain["facts"][json_name])
        for smv_name, json_name in FACT_MAPPING.items()
    }


def boolean_text(value):
    return "TRUE" if value else "FALSE"


def render_model(chain, source_name):
    facts = extract_facts(chain)
    unknown_facts = [name for name, value in facts.items() if value == UNKNOWN]
    known_facts = [name for name, value in facts.items() if value != UNKNOWN]
    lines = [
        f"-- Generated from {source_name}",
        f"-- Scenario: {chain['scenario']['id']}",
    ]

    for stage, location_value in chain["traceMap"].items():
        lines.append(
            "-- TRACE {stage}: {workflow}:{job}:{step} - {meaning}".format(
                stage=stage,
                workflow=location_value["workflowFile"],
                job=location_value["jobId"],
                step=location_value["stepIndex"],
                meaning=location_value["meaning"],
            )
        )

    lines.extend(["MODULE main"])
    if unknown_facts:
        lines.append("FROZENVAR")
        lines.extend(f"  {name} : boolean;" for name in unknown_facts)

    lines.extend(
        [
            "VAR",
            "  stage : {start, object_written, object_restored, integrity_checked, object_used, authority_reached, blocked};",
            "  object_tainted : boolean;",
        ]
    )
    if known_facts:
        lines.append("DEFINE")
        lines.extend(
            f"  {name} := {boolean_text(facts[name])};" for name in known_facts
        )

    lines.extend(
        [
            "INVAR write_succeeded -> write_authorized",
            "INVAR read_succeeded -> write_succeeded",
            "INVAR integrity_check_passed -> integrity_check_present",
            "ASSIGN",
            "  init(stage) := start;",
            "  init(object_tainted) := producer_untrusted;",
            "  next(object_tainted) := case",
            "    stage = object_restored & integrity_check_present & integrity_check_passed : FALSE;",
            "    TRUE : object_tainted;",
            "  esac;",
            "  next(stage) := case",
            "    stage = start & (!producer_untrusted | !write_intent | !write_authorized | !write_succeeded) : blocked;",
            "    stage = start : object_written;",
            "    stage = object_written & same_object & read_succeeded : object_restored;",
            "    stage = object_written : blocked;",
            "    stage = object_restored & integrity_check_present & integrity_check_passed : integrity_checked;",
            "    stage = object_restored & integrity_check_present : blocked;",
            "    stage = object_restored & consumer_uses_object : object_used;",
            "    stage = object_restored : blocked;",
            "    stage = integrity_checked & consumer_uses_object : object_used;",
            "    stage = integrity_checked : blocked;",
            "    stage = object_used & privileged_consumer & has_authority : authority_reached;",
            "    stage = object_used : blocked;",
            "    TRUE : stage;",
            "  esac;",
            "",
            "CTLSPEC AG !(stage = authority_reached & object_tainted)",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as source:
        chain = json.load(source)
    output = render_model(chain, str(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
