#!/usr/bin/env python3
"""Deterministic artifact experiment subset; never treats unmatched shell as safe."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess

import build_trust_chain
import chain_to_nusmv
import discover_artifact_chains as discovery
import evaluate_artifact_chains
import static_chain_baseline
import yaml_to_model

ALLOWED_CONSUMER_ACTIONS = {'actions/download-artifact'}
HEX_DIGEST = r'[0-9a-f]{64}'


def condition_status(value):
    """Only literal booleans are decided; expressions remain unknown."""
    value = str(value or '').strip()
    if value.startswith('${{') and value.endswith('}}'):
        value = value[3:-2].strip()
    if value in ('', 'true', 'True'):
        return 'true'
    if value in ('false', 'False'):
        return 'false'
    return 'unknown'


def and_status(*values):
    if 'false' in values:
        return 'false'
    return 'unknown' if 'unknown' in values else 'true'


def workflow_run_guard_status(value):
    """Recognize the fixed completed-success PR guard used by A1-A5."""
    literal = condition_status(value)
    if literal != 'unknown':
        return literal
    normalized = ' '.join(str(value).split())
    expected = ("github.event.workflow_run.conclusion == 'success' && "
                "github.event.workflow_run.event == 'pull_request'")
    return 'true' if normalized == expected else 'unknown'


def consumer_job(candidate):
    read = candidate['consumerOperation']
    jobs = candidate['consumerModel']['workflow']['jobs']
    return next(job for job in jobs if job['id'] == read['jobId'])


def source_fact(candidate):
    producer = candidate['producerModel']
    events = {event['name'] for event in producer['workflow']['events']}
    job = next(j for j in producer['workflow']['jobs']
               if j['id'] == candidate['producerOperation']['jobId'])
    upload = next(s for s in job['steps']
                  if s['index'] == candidate['producerOperation']['stepIndex'])
    if 'pull_request' not in events:
        return 'false' if events == {'push'} else 'unknown'
    before = [s for s in job['steps'] if s['index'] < candidate['producerOperation']['stepIndex']]
    checkouts = [s for s in before if s.get('action') == 'actions/checkout' and
                 not s.get('arguments', {}).get('ref') and not s.get('condition')]
    if len(checkouts) != 1:
        return 'unknown'
    upload_path = candidate['producerOperation'].get('path', '')
    if not relative_repo_path(upload_path):
        return 'unknown'
    copied_into_upload = False
    generated_upload = False
    for step in before:
        if step['index'] <= checkouts[0]['index']:
            continue
        if step['type'] == 'uses' and step.get('action') == 'actions/upload-artifact' and not step.get('condition'):
            continue
        if step['type'] != 'run' or step.get('shell') not in {'bash', 'sh'}:
            return 'unknown'
        for raw in step['command'].splitlines():
            line = raw.strip()
            if not line or line == 'set -eu':
                continue
            if line.startswith('grep -Eq ') and upload_path in line:
                continue
            try:
                words = shlex.split(line)
            except ValueError:
                return 'unknown'
            if words[:2] == ['mkdir', '-p'] and all(relative_repo_path(p) for p in words[2:]):
                generated_upload |= any(p == upload_path or
                    p.startswith(upload_path.rstrip('/') + '/') for p in words[2:])
                continue
            if len(words) == 3 and words[0] == 'cp' and relative_repo_path(words[1]) and \
                    relative_repo_path(words[2]) and \
                    words[2].startswith(upload_path.rstrip('/') + '/') and \
                    not words[1].startswith(upload_path.rstrip('/') + '/'):
                copied_into_upload = True
                continue
            return 'unknown'
    if copied_into_upload or not generated_upload:
        return 'true'
    return 'unknown'


def relative_repo_path(value):
    """Only literal paths within the checked-out workspace are in this subset."""
    return bool(value and not value.startswith('/') and not any(
        part in {'.', '..'} for part in value.split('/')) and
        not any(token in value for token in ('${', '$(', '`', '*', '?', ':')))


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
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    expected_branch = 'if [ "$request" = "publish=true" ]; then'
    if any(re.match(r'^(?:exit|return|exec|source|eval)\b', line) for line in lines):
        return False
    if any(line.startswith('if ') and line != expected_branch for line in lines):
        return False
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


def metadata_dummy_sink(job, read):
    """Track two artifact metadata files through step outputs to a dummy update."""
    later = [s for s in job['steps'] if s['index'] > read['stepIndex']]
    if len(later) != 2 or any(s['type'] != 'run' or s.get('shell') not in {'bash', 'sh'}
                              for s in later):
        return None, None
    metadata, sink = later
    path = read.get('path', '')
    if not path or metadata.get('env', {}).get('ARTIFACT_DIR') != path:
        return None, None
    script = metadata['command']
    for name, file in (('target_branch', 'head-ref'), ('target_sha', 'head-sha')):
        pattern = rf'{name}=\$\(tr -d [^;]+ < "\$ARTIFACT_DIR/dist-meta/{file}"\)'
        if not re.search(pattern, script):
            return None, None
        if not re.search(rf'printf \'{name}=%s\\n\' "\${name}" >> "\$GITHUB_OUTPUT"', script):
            return None, None
    if not metadata.get('id') or not all(token in script for token in
       ('case "$target_branch" in', 'case "$target_sha" in', 'exit 1',
        'test "${#target_sha}" -eq 40')):
        return None, None
    allowed_metadata = [
        r'set -eu',
        r'target_(?:branch|sha)=\$\(tr -d [^;]+ < "\$ARTIFACT_DIR/dist-meta/(?:head-ref|head-sha)"\)',
        r'case "\$target_(?:branch|sha)" in',
        r'\*\[![^]]+\]\*\|\'\'\)',
        r'echo \'invalid (?:branch name|commit SHA)\' >&2',
        r'exit 1', r';;', r'esac',
        r'test "\$\{#target_sha\}" -eq 40',
        r'printf \'target_(?:branch|sha)=%s\\n\' "\$target_(?:branch|sha)" >> "\$GITHUB_OUTPUT"',
    ]
    if not all(any(re.fullmatch(pattern, line.strip()) for pattern in allowed_metadata)
               for line in script.splitlines() if line.strip()):
        return None, None
    env = sink.get('env', {})
    for name in ('target_branch', 'target_sha'):
        expected = '${{ steps.' + metadata['id'] + '.outputs.' + name + ' }}'
        if env.get(name.upper()) != expected:
            return None, None
    if env.get('ARTIFACT_DIR') != path or sink.get('condition'):
        return None, None
    if not all(token in sink['command'] for token in
       ('test -f "$ARTIFACT_DIR/dist/index.js"',
        '$TARGET_BRANCH', '$TARGET_SHA')):
        return None, None
    if not re.search(r"echo 'dummy_repository_update_reached=(?:true|false)'",
                     sink['command']):
        return None, None
    allowed_sink = {'set -eu', 'test -f "$ARTIFACT_DIR/dist/index.js"',
                    '{', 'echo', '} | tee -a "$GITHUB_STEP_SUMMARY"'}
    if not all(line.strip() in allowed_sink or
               (line.strip().startswith('echo ') and '$(' not in line and '`' not in line)
               for line in sink['command'].splitlines() if line.strip()):
        return None, None
    return metadata, sink


def evaluate(candidate, nusmv, output, root):
    producer = candidate['producerModel']
    consumer = candidate['consumerModel']
    read = candidate['consumerOperation']
    job = consumer_job(candidate)
    producer_job = next(j for j in producer['workflow']['jobs']
                        if j['id'] == candidate['producerOperation']['jobId'])
    write_step = next(s for s in producer_job['steps']
                      if s['index'] == candidate['producerOperation']['stepIndex'])
    read_step = next(s for s in job['steps'] if s['index'] == read['stepIndex'])
    later = [s for s in job['steps'] if s['index'] > read['stepIndex']]
    unsupported = []
    for step in job['steps']:
        if step['type'] == 'uses' and step.get('action') not in ALLOWED_CONSUMER_ACTIONS:
            unsupported.append(f"unsupported action {step.get('action')}")
        elif step['index'] > read['stepIndex'] and step['type'] == 'run' and step.get('shell') not in {'bash', 'sh'}:
            unsupported.append(f"unsupported shell {step.get('shell')}")
    if candidate['pairingStatus'] != 'unique' and candidate.get('nameCompatibility') != 'equal-candidate':
        unsupported.append('ambiguous producer for artifact name/run selector')
    if candidate.get('nameCompatibility') == 'unknown':
        unsupported.append('dynamic or missing artifact name is outside the supported identity subset')
    verify, verified_uses = digest_guard(job, read['stepIndex'])
    sink_steps = [s for s in later if supported_dummy_sink(s)]
    metadata_step, metadata_sink = metadata_dummy_sink(job, read)
    use_step = sink_steps[0] if sink_steps else metadata_step
    if (guarded_uses_only(job, read['stepIndex'], verify, verified_uses)
            and sink_steps and sink_steps[0] in verified_uses):
        integrity = 'true'
        use = 'true'
    elif sink_steps:
        integrity = 'unknown' if any('sha256sum' in s.get('command', '') for s in later) else 'false'
        use = 'true'
    elif metadata_step and metadata_sink:
        integrity = 'false'
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
    metadata_sink_enabled = bool(metadata_sink and
        "echo 'dummy_repository_update_reached=true'" in metadata_sink['command'])
    sink = ('true' if sink_steps or metadata_sink_enabled else
            'false' if (use == 'false' or supported_read_only(job, read) or metadata_sink)
            and not unsupported else 'unknown')
    write_enabled = and_status(condition_status(producer_job.get('condition')),
                               condition_status(write_step.get('condition')))
    read_enabled = and_status(workflow_run_guard_status(job.get('condition')),
                              condition_status(read_step.get('condition')))
    if write_enabled == 'unknown':
        unsupported.append('producer job or upload condition is outside the supported subset')
    if read_enabled == 'unknown':
        unsupported.append('consumer job or download condition is outside the supported subset')
    if use_step:
        use_condition = condition_status(use_step.get('condition'))
        if integrity == 'true' and use_step in verified_uses:
            use_condition = 'true'
        if use_condition == 'false':
            use = 'false'
            sink = 'false'
        elif use_condition == 'unknown':
            unsupported.append('artifact use condition is outside the supported subset')
    same_object = 'false' if candidate.get('nameCompatibility') == 'literal-different' else 'unknown'
    facts = dict(producerUntrusted=source_fact(candidate), writeIntent=write_enabled,
                 writeAuthorized='unknown', writeSucceeded='unknown', sameObject=same_object,
                 readSucceeded='false' if read_enabled == 'false' else 'unknown',
                 consumerUsesObject=use,
                 integrityCheckPresent=integrity, integrityCheckPassed='unknown' if integrity == 'true' else 'false',
                 privilegedConsumer='true', hasAuthority=sink)
    if facts['producerUntrusted'] == 'unknown':
        unsupported.append('upload bytes could not be traced to the checked-out PR input')
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
    use_loc = use_step or (verified_uses[0] if verified_uses else read_step)
    sink_loc = sink_steps[0] if sink_steps else (metadata_sink or use_loc)
    trace = {'start': loc(producer_ep, write_step, 'PR artifact upload intent'),
             'object_written': loc(producer_ep, write_step, 'upload success remains unknown'),
             'object_restored': loc(consumer_ep, read_step, 'same artifact and restore success remain unknown'),
             'integrity_checked': loc(consumer_ep, verify or read_step, 'trusted digest guard' if verify else 'verification absent or unknown'),
             'object_used': loc(consumer_ep, use_loc, 'artifact-derived data use' if use_step else 'use absent or unknown'),
             'authority_reached': loc(consumer_ep, sink_loc, 'dummy publish/update marker, not real authority')}
    chain = {'scenario': {'id': candidate['id'], 'platform': 'GitHub Actions'},
             'facts': facts, 'traceMap': trace}
    smv = chain_to_nusmv.render_model(chain, candidate['id'])
    model_path = output / (candidate['id'] + '.smv')
    model_path.write_text(smv)
    run = subprocess.run([str(nusmv), str(model_path)], capture_output=True, text=True, check=True)
    formal = evaluate_artifact_chains.parse_verdict(run.stdout)
    counterexample_stages = re.findall(r'^\s*stage = ([a-z_]+)$', run.stdout, re.M) if formal == 'unsafe' else []
    independent_result = static_chain_baseline.check(chain)
    independent = independent_result['verdict']
    if formal != independent:
        raise RuntimeError(f'NuSMV/BFS disagree for {candidate["id"]}')
    if status is None:
        status = 'unsafe-counterexample' if formal == 'unsafe' else 'safe-within-model'
    event_name = 'pull_request' if facts['producerUntrusted'] == 'true' else 'unknown'
    source_file = producer['workflow']['file']
    source_text = (root / source_file).read_text()
    event_line = next((i for i, line in enumerate(source_text.splitlines(), 1)
                       if line.strip() == event_name + ':'), None)
    return {'id': candidate['id'], 'source': {'event': event_name, 'file': source_file,
            'line': event_line, 'uploadPath': candidate['producerOperation'].get('path'),
            'prByteProvenance': facts['producerUntrusted'],
            'recognition': 'literal checked-out file or recognized pre-upload cp into artifact directory'},
            'producer': producer_ep, 'consumer': consumer_ep,
            'sharedObject': {'kind': 'artifact', 'name': candidate['producerOperation'].get('name'),
                             'consumerName': candidate['consumerOperation'].get('name'),
                             'nameCompatibility': candidate.get('nameCompatibility', 'unknown'),
                             'runSelector': discovery.WORKFLOW_RUN_ID, 'sameObject': same_object,
                             'producerRunId': 'unknown', 'artifactId': 'unknown',
                             'artifactDigest': 'unknown'},
            'facts': facts, 'traceMap': trace, 'status': status,
            'hypotheticalModelVerdict': formal, 'counterexampleStages': counterexample_stages,
            'unknownAssumptions': {camel: (independent_result['witness'][snake] if independent_result['witness'] else 'unresolved')
                                   for snake,camel in chain_to_nusmv.FACT_MAPPING.items()
                                   if facts[camel] == 'unknown'},
            'unsupported': unsupported, 'simulation': 'dummy publish/update marker; no actual publish or repository write authority'}


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
