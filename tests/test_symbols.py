import unittest

from stockrec.symbols import normalize_symbol


class NormalizeSymbolTest(unittest.TestCase):
    def test_japanese_numeric_code_gets_tokyo_suffix(self):
        self.assertEqual(normalize_symbol("7203"), "7203.T")
        self.assertEqual(normalize_symbol(" 9984 "), "9984.T")
        self.assertEqual(normalize_symbol("7203.t"), "7203.T")
        self.assertEqual(normalize_symbol("25935"), "25935.T")

    def test_alphanumeric_tokyo_code_gets_suffix(self):
        self.assertEqual(normalize_symbol("285a"), "285A.T")

    def test_letter_tickers_stay_put(self):
        self.assertEqual(normalize_symbol("aapl"), "AAPL")
        self.assertEqual(normalize_symbol("BRK.B"), "BRK.B")
        self.assertEqual(normalize_symbol("^n225"), "^N225")
        self.assertEqual(normalize_symbol("0700.HK"), "0700.HK")

    def test_rejects_empty_and_unsafe_text(self):
        with self.assertRaises(ValueError):
            normalize_symbol("  ")
        with self.assertRaises(ValueError):
            normalize_symbol("../etc/passwd")
        with self.assertRaises(ValueError):
            normalize_symbol("7203;drop")


if __name__ == "__main__":
    unittest.main()
