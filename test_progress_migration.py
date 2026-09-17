import os
import tempfile
import unittest
from sqlalchemy import create_engine, inspect, text


class ProgressMigrationContractTests(unittest.TestCase):
    def test_expected_progress_columns_are_additive(self):
        # Contract-level check: the migration function source must contain only ADD COLUMN
        # statements for the four v0.6.30 fields (no destructive ALTER/DROP).
        from pathlib import Path
        source = Path(__file__).resolve().parents[1].joinpath("db.py").read_text()
        block = source.split("def ensure_task_progress_schema", 1)[1]
        self.assertIn("ADD COLUMN progress_summary", block)
        self.assertIn("ADD COLUMN waiting_on", block)
        self.assertIn("ADD COLUMN next_action", block)
        self.assertIn("ADD COLUMN last_progress_at", block)
        self.assertNotIn("DROP COLUMN", block)
        self.assertNotIn("DROP TABLE", block)


if __name__ == "__main__":
    unittest.main()
