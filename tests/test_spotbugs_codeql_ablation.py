import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from materialize_spotbugs_codeql_ablation import (  # noqa: E402
    INDIRECT_REF, INLINE_REF, OPEN_GATE, ORIGINAL_GATE, variants,
)


class SpotBugsCodeQLAblationTest(unittest.TestCase):
    def test_only_declared_lines_change(self):
        original = (Path(__file__).resolve().parents[1] /
                    'experiments/public-cases/spotbugs-chain/.github/workflows/sonarqube.yml').read_text()
        controls = variants(original)
        self.assertEqual(controls['gate-on-inline'], original.replace(INDIRECT_REF, INLINE_REF))
        self.assertEqual(controls['gate-off-indirect'], original.replace(ORIGINAL_GATE, OPEN_GATE))
        self.assertEqual(controls['gate-off-inline'], original.replace(ORIGINAL_GATE, OPEN_GATE)
                         .replace(INDIRECT_REF, INLINE_REF))

    def test_refuses_changed_source(self):
        with self.assertRaises(ValueError):
            variants('on: pull_request_target\n')


if __name__ == '__main__':
    unittest.main()
