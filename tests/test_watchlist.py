import tempfile
import unittest
from pathlib import Path

from stockrec.watchlist import ensure_in_watchlist, read_watchlist, remove_from_watchlist


class WatchlistTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "watchlist.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_comments_and_duplicate_codes_collapse(self):
        self.path.write_text("# header\n7203 # toyota\n\n7203.T\nAAPL\n", encoding="utf-8")
        self.assertEqual(read_watchlist(self.path), ["7203.T", "AAPL"])

    def test_append_preserves_comments_and_skips_duplicates(self):
        self.path.write_text("# header\n7203\n", encoding="utf-8")
        self.assertFalse(ensure_in_watchlist(self.path, "7203.T"))
        self.assertTrue(ensure_in_watchlist(self.path, "AAPL"))
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# header\n"))
        self.assertIn("AAPL\n", text)
        self.assertEqual(read_watchlist(self.path), ["7203.T", "AAPL"])

    def test_remove_keeps_other_lines(self):
        self.path.write_text("# header\n7203\nAAPL\n", encoding="utf-8")
        self.assertTrue(remove_from_watchlist(self.path, "7203.T"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "# header\nAAPL\n")
        self.assertFalse(remove_from_watchlist(self.path, "7203.T"))


if __name__ == "__main__":
    unittest.main()
