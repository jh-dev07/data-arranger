"""Full end-to-end test with the real OCR engine and ML models.
Runs only when DATA_ARRANGER_FULL=1 (CI sets it); needs models/ populated."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(os.environ.get("DATA_ARRANGER_FULL") == "1", "full test disabled")
class FullPipelineTest(unittest.TestCase):
    def test_selftest(self):
        from data_arranger import selftest
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        code = selftest.run(path)
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        self.assertEqual(code, 0, "self-test failed: %s" % {
            k: v for k, v in report.get("checks", {}).items() if not v} or report.get("error"))


if __name__ == "__main__":
    unittest.main()
