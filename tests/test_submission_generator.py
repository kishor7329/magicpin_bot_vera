import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SubmissionGeneratorTests(unittest.TestCase):
    def test_generator_writes_valid_jsonl_with_required_fields(self):
        out = ROOT / "submission.jsonl"
        if out.exists():
            out.unlink()

        subprocess.run([sys.executable, "generate_submission.py"], cwd=ROOT, check=True)

        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(rows), 25)
        for row in rows:
            self.assertTrue({"test_id", "body", "cta", "send_as", "suppression_key", "rationale"} <= set(row))
            self.assertTrue(row["body"])
            self.assertNotIn("http", row["body"].lower())
            self.assertNotIn("%%", row["body"])
            self.assertNotIn("...", row["body"])
            self.assertNotIn(" is down -", row["body"])


if __name__ == "__main__":
    unittest.main()
