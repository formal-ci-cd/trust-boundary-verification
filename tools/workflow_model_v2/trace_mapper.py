"""Map NuSMV input transitions to the active YAML step, retaining deltas."""
import re


def map_extended_trace(trace, manifest):
    state = {}
    state_id = None
    mode = None
    current_input = {}
    mapped = []
    jobs = {job['id']: job for job in manifest.get('jobs', [])}

    def flush():
        actor = current_input.get('actor')
        job = jobs.get(actor)
        if job is None:
            return
        pc = state.get('pc_' + actor, '')
        match = re.fullmatch(r's(\d+)', pc)
        if not match:
            return
        index = int(match.group(1))
        if index >= len(job['steps']):
            return
        step = job['steps'][index]
        mapped.append(dict(state=state_id, job=actor, step=step['id'], pc=pc,
                           inputs=dict(current_input), source=step['source'],
                           meaning='transition scheduled at this source step; if/outcome may skip or fail'))

    for line in trace.splitlines():
        header = re.match(r'\s*-> (State|Input): (.*?) <-', line)
        if header:
            if mode == 'Input':
                flush()
            mode = header.group(1)
            if mode == 'State':
                state_id = header.group(2)
            continue
        assignment = re.match(r'\s*([A-Za-z_][\w]*) = ([^\s]+)\s*$', line)
        if assignment:
            (current_input if mode == 'Input' else state)[assignment.group(1)] = assignment.group(2)
    if mode == 'Input':
        flush()
    return mapped


def validate_sources(manifest):
    """Check spans against saved source text and exact step locations."""
    from pathlib import Path
    problems = []
    count = 0
    groups = manifest.get('jobs', [])
    if not groups and 'typedIR' in manifest:
        groups = [manifest['typedIR'][name] for name in ('producer', 'consumer')]
    for job in groups:
        for step in job['steps']:
            source = step['source']
            lines = Path(source['file']).read_text().splitlines()
            count += 1
            if not (1 <= source['line'] <= len(lines)):
                problems.append(source)
            elif not lines[source['line'] - 1].lstrip().startswith('- '):
                problems.append(source)
    return dict(checkedSteps=count, invalidSpans=problems)
