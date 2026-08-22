import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "model_to_nusmv.py"
SPEC = importlib.util.spec_from_file_location("model_to_nusmv", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ModelToNuSMVTest(unittest.TestCase):
    def load(self, name):
        with (ROOT / "model" / "examples" / name).open(encoding="utf-8") as source:
            return json.load(source)

    def test_gha_c1_has_denied_and_failed_write(self):
        facts = MODULE.extract_facts(self.load("gha-c1.json"))

        self.assertTrue(facts["write_intent"])
        self.assertFalse(facts["write_authorized"])
        self.assertFalse(facts["write_succeeded"])
        self.assertEqual(facts["same_object"], MODULE.UNKNOWN)

    def test_gha_c2_keeps_unconfirmed_facts_unknown(self):
        facts = MODULE.extract_facts(self.load("gha-c2.json"))

        self.assertTrue(facts["write_intent"])
        self.assertEqual(facts["write_authorized"], MODULE.UNKNOWN)
        self.assertEqual(facts["write_succeeded"], MODULE.UNKNOWN)
        self.assertEqual(facts["consumer_uses_object"], MODULE.UNKNOWN)

    def test_unknown_fact_becomes_frozen_variable(self):
        facts = MODULE.extract_facts(self.load("gha-c2.json"))
        rendered = MODULE.render_model(facts, "gha-c2.json")

        self.assertIn("FROZENVAR", rendered)
        self.assertIn("same_object : boolean;", rendered)
        self.assertIn(
            "INVAR write_succeeded -> (write_intent & write_authorized)", rendered
        )
        self.assertIn("CTLSPEC AG stage != authority_reached", rendered)

    def test_consumer_only_model_is_not_used_as_producer(self):
        with self.assertRaises(ValueError):
            MODULE.extract_facts(self.load("gha-c2-consumer.json"))


if __name__ == "__main__":
    unittest.main()
