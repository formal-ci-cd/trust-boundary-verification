import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import compare_spotbugs_outputs as comparison


ROOT = Path(__file__).resolve().parents[1]


class SpotbugsComparisonTests(unittest.TestCase):
    def test_saved_outputs_link_no_full_path(self):
        report = comparison.compare(ROOT)
        self.assertEqual(report['proposed']['originalPathCount'], 1)
        self.assertEqual(report['proposed']['safeRefControlPathCount'], 0)
        self.assertTrue(report['baselines'])
        self.assertTrue(all(value['sameFindingLinksAllStages'] == 0
                            for value in report['baselines'].values()))
        self.assertGreater(report['baselines']['sisakulint-0.3.7.sarif.gz']
                           ['stageFindingCounts']['checkout'], 0)

    def test_related_step_region_does_not_imply_next_step(self):
        result = {'locations': [], 'relatedLocations': [{
            'physicalLocation': {'artifactLocation': {'uri': 'sonarqube.yml'},
                                 'region': {'startLine': 35, 'endLine': 42}}}]}
        self.assertEqual(comparison.locations(result), {35})


if __name__ == '__main__':
    unittest.main()
