import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import get_env_float, get_env_int, get_env_int_list, is_env_enabled


class TestEnvInt(unittest.TestCase):
    def _get(self, raw, default=7):
        with patch.dict(os.environ, {"X_TEST_INT": raw}):
            return get_env_int("X_TEST_INT", default)

    def test_missing_key_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_INT", None)
            self.assertEqual(get_env_int("X_TEST_INT", 42), 42)
            self.assertEqual(get_env_int("X_TEST_INT"), 0)

    def test_plain_and_padded_integers(self):
        self.assertEqual(self._get("12"), 12)
        self.assertEqual(self._get("  12  "), 12)
        self.assertEqual(self._get("0"), 0)

    def test_negative_integers_are_parsed(self):
        self.assertEqual(self._get("-5"), -5)
        self.assertEqual(self._get(" -1 "), -1)

    def test_malformed_values_return_default(self):
        for raw in ("", "   ", "abc", "1.5", "1e3", "--3", "12abc", "²"):
            with self.subTest(raw=raw):
                self.assertEqual(self._get(raw), 7)


class TestEnvFloat(unittest.TestCase):
    def _get(self, raw, default=2.5):
        with patch.dict(os.environ, {"X_TEST_FLOAT": raw}):
            return get_env_float("X_TEST_FLOAT", default)

    def test_missing_key_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_FLOAT", None)
            self.assertEqual(get_env_float("X_TEST_FLOAT", 1.25), 1.25)

    def test_valid_floats(self):
        self.assertEqual(self._get("3.0"), 3.0)
        self.assertEqual(self._get(" 10 "), 10.0)
        self.assertEqual(self._get("-0.5"), -0.5)

    def test_malformed_values_return_default(self):
        for raw in ("", "abc", "1,5"):
            with self.subTest(raw=raw):
                self.assertEqual(self._get(raw), 2.5)

    def test_non_finite_values_return_default(self):
        for raw in ("nan", "NaN", "inf", "-inf", "Infinity"):
            with self.subTest(raw=raw):
                self.assertEqual(self._get(raw), 2.5)


class TestEnvIntList(unittest.TestCase):
    def _get(self, raw):
        with patch.dict(os.environ, {"X_TEST_LIST": raw}):
            return get_env_int_list("X_TEST_LIST")

    def test_missing_and_empty(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_LIST", None)
            self.assertEqual(get_env_int_list("X_TEST_LIST"), [])
        self.assertEqual(self._get(""), [])
        self.assertEqual(self._get("  "), [])

    def test_single_and_multiple_preserve_order(self):
        self.assertEqual(self._get("5"), [5])
        self.assertEqual(self._get("3, 1,2"), [3, 1, 2])

    def test_invalid_entries_are_skipped(self):
        self.assertEqual(self._get("1,,abc, 2,"), [1, 2])


class TestEnvEnabled(unittest.TestCase):
    def test_truthy_and_falsy_spellings(self):
        for raw, expected in (
            ("True", True), ("true", True), (" 1 ", True), ("t", True),
            ("False", False), ("0", False), ("", False), ("yes", False),
        ):
            with self.subTest(raw=raw), patch.dict(os.environ, {"X_TEST_FLAG": raw}):
                self.assertIs(is_env_enabled("X_TEST_FLAG"), expected)

    def test_missing_key_uses_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_TEST_FLAG", None)
            self.assertIs(is_env_enabled("X_TEST_FLAG"), True)
            self.assertIs(is_env_enabled("X_TEST_FLAG", default=False), False)


if __name__ == "__main__":
    unittest.main()
