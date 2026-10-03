#!/usr/bin/env python3
"""Deterministic artifact experiment subset; never treats unmatched shell as safe."""
import argparse
import json
from pathlib import Path
import re
import subprocess

import build_trust_chain
import chain_to_nusmv
import discover_artifact_chains as discovery
import evaluate_artifact_chains
import static_chain_baseline
import yaml_to_model

ALLOWED_CONSUMER_ACTIONS = {'actions/download-artifact'}
HEX_DIGEST = r'[0-9a-f]{64}'


def consumer_job(candidate):
    read = candidate['consumerOperation']
    jobs = candidate['consumerModel']['workflow']['jobs']
    return next(job for job in jobs if job['id'] == read['jobId'])


def source_fact(candidate):
    producer = candidate['producerModel']
    events = {event['name'] for event in producer['workflow']['events']}
    job = next(j for j in producer['workflow']['jobs']
               if j['id'] == candidate['producerOperation']['jobId'])
    before = [s for s in job['steps'] if s['index'] < candidate['producerOperation']['stepIndex']]
    checkout = any(s.get('action') == 'actions/checkout' and
                   not s.get('arguments', {}).get('ref') for s in before)
    if 'pull_request' in events and checkout:
        return 'true'
    return 'unknown'


def digest_guard(job, read_index):
    """Recognize only an exact trusted literal digest, output, and guarded later use."""
    later = [s for s in job['steps'] if s['index'] > read_index]
    for verify in later:
        if verify['type'] != 'run' or verify.get('shell') not in {'bash', 'sh'}:
            continue
        script = verify['command']
        if not re.search(r"expected_digest='" + HEX_DIGEST + r"'", script):
            continue
        if not re.search(r'actual_digest=\$\(sha256sum "\$ARTIFACT_FILE" \| cut -d ', script):
            continue
        if not re.search(r'if \[ "\$actual_digest" = "\$expected_digest" \]; then\s+verified=true\s+else\s+verified=false\s+fi', script):
            continue
        if not re.search(r"printf 'verified=%s\\n' \"\$verified\" >> \"\$GITHUB_OUTPUT\"", script):
            continue
        recognized = [
            r'set -eu', r"expected_digest='[0-9a-f]{64}'",
            r'actual_digest=\$\(sha256sum "\$ARTIFACT_FILE" \| cut -d \' \' -f 1\)',
            r'if \[ "\$actual_digest" = "\$expected_digest" \]; then',
            r'verified=true', r'else', r'verified=false', r'fi',
            r'printf \'verified=%s\\n\' "\$verified" >> "\$GITHUB_OUTPUT"',
            r'printf \'actual_digest=%s\\n\' "\$actual_digest" >> "\$GITHUB_OUTPUT"',
        ]
        lines = [line.strip() for line in script.splitlines() if line.strip()]
        if len(lines) != len(recognized) or not all(
                re.fullmatch(pattern, line) for pattern, line in zip(recognized, lines)):
            continue
        if not verify.get('id'):
            continue
        guard = f"steps.{verify['id']}.outputs.verified == 'true'"
        uses = [s for s in later if s['index'] > verify['index'] and
                s.get('condition', '').strip() == guard and 'ARTIFACT_FILE' in s.get('env', {})]
        if uses and all(verify['index'] < u['index'] and u.get('shell') in {'bash', 'sh'} for u in uses):
            return verify, uses
    return None, []


def supported_dummy_sink(step):
    """Research marker only; never equate it with an actual publish permission."""
    script = step.get('command', '')
    return (step['type'] == 'run' and
            re.search(r'if \[ "\$request" = "publish=true" \]; then\s+authority_reached=true\s+else\s+authority_reached=false\s+fi', script) is not None and
            'dummy_publish_authority_reached=$authority_reached' in script and
            re.search(r'request=\$\(tr -d .* < "\$ARTIFACT_FILE"\)', script) is not None)


def supported_no_use(job, read):
    """Small literal summary-only shell contract; every post-download step is checked."""
    path = read.get('path', '')
    for step in job['steps']:
        if step['index'] <= read['stepIndex']:
            continue
        if step['type'] != 'run' or step.get('shell') not in {'bash', 'sh'}:
            return False
        if any(path in value or 'ARTIFACT_FILE' in value
               for value in step.get('env', {}).values()):
            return False
        script = step['command']
        if any(token in script for token in ('ARTIFACT_FILE', path, 'source ', 'eval ', 'bash ', 'sh ', 'cat ', 'tr ', 'git ', 'npm ', 'pnpm ')):
            return False
        lines = [line.strip() for line in script.splitlines() if line.strip()]
        if not all(line in {'{', '}', '} | tee -a "$GITHUB_STEP_SUMMARY"', 'echo'} or
                   (line.startswith(('echo ', 'printf ')) and '$(' not in line and '`' not in line)
                   for line in lines):
            return False
    return True


def supported_read_only(job, read):
    """Recognize the artifact-read plus literal false marker simulation only."""
    later = [s for s in job['steps'] if s['index'] > read['stepIndex']]
    if len(later) != 1 or later[0]['type'] != 'run' or later[0].get('shell') not in {'bash', 'sh'}:
        return False
    step = later[0]
    if not any(read.get('path', '') in value for value in step.get('env', {}).values()):
        return False
    lines = [line.strip() for line in step['command'].splitlines() if line.strip()]
    reads = [line for line in lines if re.fullmatch(
        r'request=\$\(tr -d [^;]+ < "\$ARTIFACT_FILE"\)', line)]
    if len(reads) != 1 or not any("echo 'dummy_publish_authority_reached=false'" == line for line in lines):
        return False
    for line in lines:
        if line in {'set -eu', '{', '}', 'echo', '} | tee -a "$GITHUB_STEP_SUMMARY"'} or line in reads:
            continue
        if line.startswith('echo ') and '$(' not in line and '`' not in line:
            continue
        return False
    return True


def guarded_uses_only(job, read_index, verify, uses):
    """An extra artifact read or unguarded sink voids a safe verification claim."""
    if not verify or not uses:
        return False
    guarded = {step['index'] for step in uses}
    for step in job['steps']:
        if step['index'] <= read_index or step['index'] == verify['index']:
            continue
        if step['type'] != 'run':
            return False
        if 'ARTIFACT_FILE' in step.get('env', {}) and step['index'] not in guarded:
            return False
        if supported_dummy_sink(step) and step['index'] not in guarded:
            return False
        if step['index'] not in guarded:
            lines = [line.strip() for line in step.get('command', '').splitlines() if line.strip()]
            if not all(line in {'{', '}', 'echo', '} >> "$GITHUB_STEP_SUMMARY"',
                                '} | tee -a "$GITHUB_STEP_SUMMARY"'} or
                       (line.startswith(('echo ', 'printf ')) and '$(' not in line and '`' not in line)
                       for line in lines):
                return False
    return True


def evaluate(candidate, nusmv, output, root):
    producer = candidate['producerModel']
    consumer = candidate['consumerModel']
    read = candidate['consumerOperation']
    job = consumer_job(candidate)
    later = [s for s in job['steps'] if s['index'] > read['stepIndex']]
    unsupported = []
    for step in later:
        if step['type'] == 'uses' and step.get('action') not in ALLOWED_CONSUMER_ACTIONS:
            unsupported.append(f"unsupported action {step.get('action')}")
        elif step['type'] == 'run' and step.get('shell') not in {'bash', 'sh'}:
            unsupported.append(f"unsupported shell {step.get('shell')}")
    if candidate['pairingStatus'] != 'unique':
        unsupported.append('ambiguous producer for artifact name/run selector')
    verify, verified_uses = digest_guard(job, read['stepIndex'])
    sink_steps = [s for s in later if supported_dummy_sink(s)]
    use_step = sink_steps[0] if sink_steps else None
    if (guarded_uses_only(job, read['stepIndex'], verify, verified_uses)
            and sink_steps and sink_steps[0] in verified_uses):
        integrity = 'true'
        use = 'true'
    elif sink_steps:
        integrity = 'unknown' if any('sha256sum' in s.get('command', '') for s in later) else 'false'
        use = 'true'
    elif supported_no_use(job, read):
        integrity = 'false'
        use = 'false'
    elif supported_read_only(job, read):
        integrity = 'false'
        use = 'true'
    else:
        integrity = 'unknown'
        use = 'unknown'
        unsupported.append('artifact use or verification is outside the supported shell subset')
    # A dummy sink is a research simulation and has no real publish authority.
    # Its own property is evaluated separately from configured GitHub permissions.
    sink = 'true' if sink_steps else ('false' if (use == 'false' or supported_read_only(job, read)) and not unsupported else 'unknown')
    facts = dict(producerUntrusted=source_fact(candidate), writeIntent='true',
                 writeAuthorized='unknown', writeSucceeded='unknown', sameObject='unknown',
                 readSucceeded='unknown', consumerUsesObject=use,
                 integrityCheckPresent=integrity, integrityCheckPassed='unknown' if integrity == 'true' else 'false',
                 privilegedConsumer='true', hasAuthority=sink)
    if unsupported:
        status = 'unknown/unsupported'
    else:
        status = None
    producer_ep = build_trust_chain.endpoint(producer, candidate['producerOperation'])
    consumer_ep = build_trust_chain.endpoint(consumer, read)
    def loc(ep, step, meaning):
        value = build_trust_chain.location(ep, step['index'], meaning)
        value['line'] = step['line']
        return value
    write_step = next(s for j in producer['workflow']['jobs'] if j['id'] == candidate['producerOperation']['jobId']
                      for s in j['steps'] if s['index'] == candidate['producerOperation']['stepIndex'])
    read_step = next(s for s in job['steps'] if s['index'] == read['stepIndex'])
    use_loc = use_step or (verified_uses[0] if verified_uses else read_step)
    sink_loc = sink_steps[0] if sink_steps else use_loc
    trace = {'start': loc(producer_ep, write_step, 'PR artifact upload intent'),
             'object_written': loc(producer_ep, write_step, 'upload success remains unknown'),
             'object_restored': loc(consumer_ep, read_step, 'same artifact and restore success remain unknown'),
             'integrity_checked': loc(consumer_ep, verify or read_step, 'trusted digest guard' if verify else 'verification absent or unknown'),
             'object_used': loc(consumer_ep, use_loc, 'artifact-derived dummy publish decision' if use_step else 'use absent or unknown'),
             'authority_reached': loc(consumer_ep, sink_loc, 'dummy publish marker, not real authority')}
    chain = {'scenario': {'id': candidate['id'], 'platform': 'GitHub Actions'},
             'facts': facts, 'traceMap': trace}
    smv = chain_to_nusmv.render_model(chain, candidate['id'])
    model_path = output / (candidate['id'] + '.smv')
    model_path.write_text(smv)
    run = subprocess.run([str(nusmv), str(model_path)], capture_output=True, text=True, check=True)
    formal = evaluate_artifact_chains.parse_verdict(run.stdout)
    counterexample_stages = re.findall(r'^\s*stage = ([a-z_]+)$', run.stdout, re.M) if formal == 'unsafe' else []
    independent = static_chain_baseline.check(chain)['verdict']
    if formal != independent:
        raise RuntimeError(f'NuSMV/BFS disagree for {candidate["id"]}')
    if status is None:
        status = 'unsafe-counterexample' if formal == 'unsafe' else 'safe-within-model'
    event_name = 'pull_request' if facts['producerUntrusted'] == 'true' else 'unknown'
    source_file = producer['workflow']['file']
    source_text = (root / source_file).read_text()
    event_line = next((i for i, line in enumerate(source_text.splitlines(), 1)
                       if line.strip() == event_name + ':'), None)
    return {'id': candidate['id'], 'source': {'event': event_name, 'file': source_file, 'line': event_line},
            'producer': producer_ep, 'consumer': consumer_ep,
            'sharedObject': {'kind': 'artifact', 'name': candidate['producerOperation'].get('name'),
                             'runSelector': discovery.WORKFLOW_RUN_ID, 'sameObject': 'unknown'},
            'facts': facts, 'traceMap': trace, 'status': status,
            'hypotheticalModelVerdict': formal, 'counterexampleStages': counterexample_stages,
            'unknownAssumptions': sorted(k for k,v in facts.items() if v == 'unknown'),
            'unsupported': unsupported, 'simulation': 'dummy publish marker; no actual publish authority'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--nusmv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    models = yaml_to_model.load_models(args.root)
    candidates = discovery.discover_candidates(models)
    results = [evaluate(c, args.nusmv, args.output, args.root) for c in candidates]
    report = {'scope': 'artifact dummy publish subset only; no actual authority or runtime success inferred',
              'candidateCount': len(candidates), 'emptyCandidateMeaning': 'unknown/unsupported',
              'results': results}
    (args.output / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps([(r['consumer']['workflow']['file'],r['status'],r['hypotheticalModelVerdict']) for r in results]))


if __name__ == '__main__':
    main()
