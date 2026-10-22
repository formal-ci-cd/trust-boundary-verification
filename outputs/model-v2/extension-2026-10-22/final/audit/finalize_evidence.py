from pathlib import Path
import json
import re

root, out = Path('/repo'), Path('/out')
summary = json.loads((out / 'verification-summary.json').read_text())
comparisons = []
for model in summary['models']:
    if not model['id'].startswith('legacy-'):
        continue
    slug = model['id'][7:]
    old_dir = root / 'outputs/model-v2/mutations' / slug
    old = dict(re.findall(r'^-- specification (.*?)\s+is (true|false)\s*$', (old_dir / (slug + '.stdout.txt')).read_text(), re.M))
    shared = sorted(old.keys() & model['properties'].keys())
    matches = {key: old[key] == model['properties'][key] for key in shared}
    old_exit = int((old_dir / (slug + '.exitcode')).read_text())
    comparisons.append(dict(case=slug, sharedProperties=matches, oldExit=old_exit, newExit=model['exitcode'],
                            matched=all(matches.values()) and old_exit == model['exitcode']))
(out / 'legacy-mutation-comparison.json').write_text(json.dumps(comparisons, indent=2) + '\n')
before = set(Path('/audit/git-status-before.txt').read_text().splitlines())
after = set((out / 'audit/git-status-after.txt').read_text().splitlines())
new_entries = sorted(after - before)
(out / 'audit/change-inventory.json').write_text(json.dumps(dict(
    modifiedExistingFiles=['tools/workflow_model_v2/builder.py', 'tools/workflow_to_smv_v2.py'],
    newlyUntrackedEntries=new_entries), indent=2) + '\n')
report = (out / 'extension-report-2026-10-22.md').read_text()
report += '\n## 補足：既存mutationとの比較と変更一覧\n\n'
report += f'保存済み21 mutationの共通propertyとexit codeを比較し，{sum(c["matched"] for c in comparisons)}/21件一致した．完全な比較は [legacy-mutation-comparison.json](../../outputs/model-v2/extension-2026-10-22/legacy-mutation-comparison.json)，変更項目は [change-inventory.json](../../outputs/model-v2/extension-2026-10-22/audit/change-inventory.json)．\n'
report = report.replace('extension-2026-10-22/legacy-mutation', 'extension-2026-10-22/final/legacy-mutation').replace('extension-2026-10-22/audit/change-inventory', 'extension-2026-10-22/final/audit/change-inventory')
(out / 'extension-report-2026-10-22.md').write_text(report)
(out / 'README.md').write_text(report.replace('../../outputs/model-v2/extension-2026-10-22/final/', '')
                             .replace('(extension-semantics.md)', '(../../../../docs/next-model-design/extension-semantics.md)'))
print(json.dumps({'legacyMutationsCompared': len(comparisons), 'mismatches': [c['case'] for c in comparisons if not c['matched']]}))
if not all(c['matched'] for c in comparisons):
    raise SystemExit(1)
