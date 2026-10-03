#!/usr/bin/env python3
"""Compare the scope of saved TanStack SARIF findings with the model path.

This counts files actually attached to each SARIF result, including code-flow
and related locations. It does not infer a tool's theoretical capability or
equate a local entry warning with a complete cross-workflow attack path.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


INPUTS = {
    "codeql-incident-available-default": {
        "pre-incident": "results/tanstack-cache-chain/historical-codeql-2026-05/pre-incident-default.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/historical-codeql-2026-05/mitigation-default.sarif.gz",
    },
    "codeql-current-security-and-quality": {
        "pre-incident": "results/tanstack-cache-chain/pre-incident/codeql-security-and-quality.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/mitigation/codeql-security-and-quality.sarif.gz",
    },
    "zizmor-current-regular": {
        "pre-incident": "results/tanstack-cache-chain/pre-incident/zizmor-regular.sarif.gz",
        "mitigation": "results/tanstack-cache-chain/mitigation/zizmor-regular.sarif.gz",
    },
    "sisakulint-current": {
        "pre-incident": "results/additional-sisakulint-baseline/tanstack-pre-incident.sarif.gz",
        "mitigation": "results/additional-sisakulint-baseline/tanstack-mitigation.sarif.gz",
    },
}
PRODUCER = "bundle-size.yml"
CONSUMER = "release.yml"


def location_files(result):
    """Read every SARIF physicalLocation, including nested thread flows."""
    found = set()

    def visit(value):
        if isinstance(value, dict):
            physical = value.get("physicalLocation")
            if isinstance(physical, dict):
                uri = physical.get("artifactLocation", {}).get("uri")
                if uri:
                    found.add(Path(uri).name)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return sorted(found)


def property_level_class(producer_count, consumer_count, same_count):
    if same_count:
        # Two locations in one SARIF result alone do not prove the intermediate
        # cache object or authority dependence.
        return 'same-finding-two-endpoints; full-path semantics require review'
    if producer_count and consumer_count:
        return '入口と出口を別findingとして検出'
    if producer_count:
        return '入口のみ'
    if consumer_count:
        return '出口のみ'
    return '対象findingなし'


def summarize_sarif(path, tool_name):
    compressed = path.read_bytes()
    sarif = json.loads(gzip.decompress(compressed))
    results = [result for run in sarif["runs"] for result in run.get("results", [])]
    entries = []
    for result in results:
        files = location_files(result)
        entries.append(
            {
                "rule": result["ruleId"],
                "files": files,
                "mentionsConsumerInMessage": CONSUMER in result.get("message", {}).get("text", ""),
            }
        )
    producer_count = sum(PRODUCER in item["files"] for item in entries)
    consumer_count = sum(CONSUMER in item["files"] for item in entries)
    same_count = sum(PRODUCER in item["files"] and CONSUMER in item["files"]
                     for item in entries)
    entry_rules = {
        'codeql-incident-available-default': {'actions/cache-poisoning/poisonable-step',
                                               'actions/untrusted-checkout/medium'},
        'codeql-current-security-and-quality': {'actions/cache-poisoning/poisonable-step',
                                               'actions/untrusted-checkout/medium'},
        'zizmor-current-regular': {'zizmor/dangerous-triggers'},
        'sisakulint-current': {'untrusted-checkout'},
    }[tool_name]
    target_entry = sum(PRODUCER in item['files'] and item['rule'] in entry_rules
                       for item in entries)
    target_exit = 0  # No saved rule identifies this cache-derived privileged sink.
    return {
        "inputSHA256": hashlib.sha256(compressed).hexdigest(),
        "findingCount": len(entries),
        "targetEntryFindingCount": target_entry,
        "targetExitFindingCount": target_exit,
        "endpointLocationClass": property_level_class(producer_count, consumer_count, same_count),
        "producerFindingCount": producer_count,
        "consumerFindingCount": consumer_count,
        "sameFindingLinksProducerAndConsumer": same_count,
        "propertyLevelClass": property_level_class(target_entry, target_exit, 0),
        "producerFindingMentionsConsumer": sum(
            PRODUCER in item["files"] and item["mentionsConsumerInMessage"]
            for item in entries
        ),
        "findingsAtProducerOrConsumer": [
            item for item in entries if PRODUCER in item["files"] or CONSUMER in item["files"]
        ],
    }


def summarize_poutine(path, expected_sha256):
    compressed = path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != expected_sha256:
        raise ValueError(f'Poutine evidence checksum mismatch: {path}')
    data = json.loads(gzip.decompress(compressed))
    entries = [{'rule': item['rule_id'],
                'files': [Path(item.get('meta', {}).get('path', '')).name]}
               for item in data['findings']]
    producer_count = sum(PRODUCER in item['files'] for item in entries)
    consumer_count = sum(CONSUMER in item['files'] for item in entries)
    same_count = sum(PRODUCER in item['files'] and CONSUMER in item['files']
                     for item in entries)
    return {'inputSHA256': expected_sha256, 'findingCount': len(entries),
            'producerFindingCount': producer_count,
            'consumerFindingCount': consumer_count,
            'sameFindingLinksProducerAndConsumer': same_count,
            'endpointLocationClass': property_level_class(producer_count, consumer_count, same_count),
            'propertyLevelClass': '対象findingなし',
            'classificationBasis': 'Poutine unverified-creator warnings on both files do not assert the PR-to-cache entry or privileged sink',
            'findingsAtProducerOrConsumer': [item for item in entries
                                              if PRODUCER in item['files'] or CONSUMER in item['files']]}


def compare(root):
    analysis_path = root / "results/tanstack-cache-chain/analysis.json"
    analysis = json.loads(analysis_path.read_text())
    cases = {case["variant"]: case for case in analysis["cases"]}
    model = {}
    for variant in ("pre-incident", "mitigation"):
        case = cases[variant]
        evidence = sorted({Path(item["file"]).name for item in case["evidence"]})
        if not {PRODUCER, CONSUMER}.issubset(evidence):
            raise ValueError(f"Model evidence lacks endpoints: {variant}")
        if case["bfs"]["verdict"] != case["nusmv"]["verdict"]:
            raise ValueError(f"Independent BFS/NuSMV disagree: {variant}")
        model[variant] = {
            "staticVerdict": case["staticVerdict"],
            "formalVerdict": case["nusmv"]["verdict"],
            "evidenceFiles": evidence,
            "conditionalCounterexampleSteps": [
                item["step"] for item in case["bfs"]["conditionalCounterexample"]
            ],
        }
    common_subset = {}
    for variant, directory in [('pre-incident', 'common-path-pre'),
                               ('mitigation', 'common-path-mitigation')]:
        path = root / 'results/tanstack-cache-chain' / directory / 'analysis.json'
        report = json.loads(path.read_text())
        matching = [item for item in report['results']
                    if Path(item['producer']['file']).name == PRODUCER
                    and item['producer']['job'] == 'benchmark-pr'
                    and Path(item['consumer']['file']).name == CONSUMER]
        if len(matching) != 1:
            raise ValueError(f'Expected one common cache path: {variant}: {len(matching)}')
        item = matching[0]
        files = sorted({Path(e['file']).name for e in item['evidence']})
        if not {PRODUCER, CONSUMER, 'action.yml'}.issubset(files):
            raise ValueError(f'Common cache path lacks evidence: {variant}')
        common_subset[variant] = {
            'input': str(path.relative_to(root)), 'status': item['status'],
            'property': 'Untrusted cache bytes do not reach an explicit repository-write sink in a distinct release run',
            'evidenceFiles': files, 'sameObject': item['sharedObject']['sameObject'],
            'unknownAssumptions': item['unknownAssumptions'],
            'counterexampleLength': len(item['order']['counterexample']),
            'scope': 'per-path finite model; actual bytes, execution and write success unobserved',
        }
    tools = {}
    for name, paths in INPUTS.items():
        tools[name] = {}
        for variant, relative in paths.items():
            tools[name][variant] = {"input": relative, **summarize_sarif(root / relative, name)}
    poutine_index = json.loads((root / 'results/additional-poutine-baseline/evidence-index.json').read_text())
    poutine = {}
    for variant, case_name in [('pre-incident', 'tanstack-pre'),
                               ('mitigation', 'tanstack-mitigation')]:
        record = next(case for case in poutine_index['cases'] if case['name'] == case_name)
        relative = 'results/additional-poutine-baseline/' + record['result']
        poutine[variant] = {'input': relative,
                            **summarize_poutine(root / relative, record['sha256'])}
    tools['poutine-1.1.6-retrospective'] = poutine
    return {
        "property": analysis["property"],
        "scope": "Fixed TanStack incident and mitigation snapshots; only the listed SARIF runs and saved model output",
        "interpretation": "A zero same-finding count describes these emitted SARIF results, not inability to implement a custom query or absence of local warnings. Model counterexample is conditional on runtime facts; incident observations are separate.",
        "producerFile": PRODUCER,
        "consumerFile": CONSUMER,
        "model": model,
        "commonSubset": common_subset,
        "tools": tools,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.root), indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
