"""Run NuSMV explicitly, preserving each model's stdout, stderr and statistics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time


def verify(model, executable='NuSMV'):
    model = Path(model).resolve()
    if not model.is_file():
        raise FileNotFoundError(model)
    outputs = {suffix: Path(str(model) + suffix) for suffix in
               ('.stdout', '.stderr', '.exitcode', '.seconds', '.timing', '.commands',
                '.stats.stdout', '.stats.stderr', '.stats.exitcode')}
    for path in outputs.values():
        if path.exists():
            raise FileExistsError(f'result already exists; use a new output directory: {path}')
    started = time.monotonic()
    with outputs['.stdout'].open('x') as stdout, outputs['.stderr'].open('x') as stderr:
        result = subprocess.run([executable, '-ctt', str(model)], stdout=stdout, stderr=stderr)
    elapsed = time.monotonic() - started
    outputs['.exitcode'].write_text(str(result.returncode) + '\n')
    outputs['.seconds'].write_text(str(int(elapsed)) + '\n')
    outputs['.timing'].write_text(f'real 0m{elapsed:.6f}s\n')
    stats_code = None
    if result.returncode == 0:
        # NuSMV command syntax accepts a quoted input path.
        outputs['.commands'].write_text(
            f'read_model -i {json.dumps(str(model))}\ngo\ncompute_reachable\nprint_reachable_states\nquit\n')
        with outputs['.stats.stdout'].open('x') as stdout, outputs['.stats.stderr'].open('x') as stderr:
            stats = subprocess.run([executable, '-source', str(outputs['.commands'])], stdout=stdout, stderr=stderr)
        stats_code = stats.returncode
        outputs['.stats.exitcode'].write_text(str(stats_code) + '\n')
    print(f'NuSMV exit={result.returncode}, stats={stats_code}: {model}', flush=True)
    return result.returncode, stats_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='one .smv file, or an evaluation directory containing inventory.json')
    args = parser.parse_args()
    try:
        if args.target.is_dir():
            inventory = json.loads((args.target / 'inventory.json').read_text())
            failed = False
            for item in inventory:
                code, stats_code = verify(args.target / item['model'])
                intentional = item['id'] == 'legacy-nusmv-invalid-input'
                failed |= (code == 0) if intentional else (code != 0 or stats_code != 0)
            return int(failed)
        code, stats_code = verify(args.target)
        return int(code != 0 or stats_code != 0)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
