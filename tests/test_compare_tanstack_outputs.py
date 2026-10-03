import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import compare_tanstack_outputs as comparison


class TanStackOutputComparisonTests(unittest.TestCase):
    def test_nested_code_flow_can_link_two_workflows(self):
        result = {
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": ".github/workflows/bundle-size.yml"}}}
            ],
            "codeFlows": [
                {"threadFlows": [{"locations": [
                    {"location": {"physicalLocation": {
                        "artifactLocation": {"uri": ".github/workflows/release.yml"}
                    }}}
                ]}]}
            ],
        }
        self.assertEqual(
            comparison.location_files(result),
            ["bundle-size.yml", "release.yml"],
        )

    def test_fixed_incident_comparison_keeps_local_warning_distinct_from_chain(self):
        report = comparison.compare(ROOT)
        codeql = report["tools"]["codeql-incident-available-default"]
        self.assertEqual(codeql["pre-incident"]["producerFindingCount"], 1)
        self.assertEqual(codeql["pre-incident"]["sameFindingLinksProducerAndConsumer"], 0)
        self.assertEqual(codeql["pre-incident"]["propertyLevelClass"], "入口のみ")
        self.assertEqual(report["model"]["pre-incident"]["formalVerdict"], "possible")
        self.assertEqual(report["commonSubset"]["pre-incident"]["status"], "unsafe-counterexample")
        self.assertEqual(report["commonSubset"]["mitigation"]["status"], "safe-within-model")
        self.assertEqual(report["commonSubset"]["pre-incident"]["sameObject"], "unknown")
        self.assertEqual(report["tools"]["poutine-1.1.6-retrospective"]["pre-incident"]["sameFindingLinksProducerAndConsumer"], 0)
        self.assertEqual(report["tools"]["poutine-1.1.6-retrospective"]["pre-incident"]["propertyLevelClass"], "対象findingなし")
        self.assertEqual(
            report["model"]["mitigation"]["formalVerdict"],
            "no-path-in-supported-model",
        )


if __name__ == "__main__":
    unittest.main()
