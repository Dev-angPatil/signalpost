"""A JSONL row must stay on one line for every line-based reader."""
import json
import unittest

import run_agent


class JsonlLineBreaks(unittest.TestCase):
    def test_unicode_line_separators_are_escaped(self):
        row = {"organisation_number": "923609016", "text": "a b c\x85d\x0ce"}
        line = run_agent.jsonl_line(row)
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(json.loads(line), row)


if __name__ == "__main__":
    unittest.main()
