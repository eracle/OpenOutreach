# tests/test_emails.py
from linkedin.api.emails import normalize_boolean


class TestNormalizeBoolean:
    def test_none_returns_none(self):
        assert normalize_boolean(None) is None

    def test_bool_passthrough(self):
        assert normalize_boolean(True) is True
        assert normalize_boolean(False) is False

    def test_string_true_variants(self):
        for val in ("true", "True", "TRUE", "t", "yes", "y", "1", "on"):
            assert normalize_boolean(val) is True, f"Failed for {val!r}"

    def test_string_false_variants(self):
        for val in ("false", "False", "FALSE", "f", "no", "n", "0", "off", ""):
            assert normalize_boolean(val) is False, f"Failed for {val!r}"

    def test_string_with_whitespace(self):
        assert normalize_boolean("  true  ") is True
        assert normalize_boolean("  false  ") is False

    def test_unrecognized_string_returns_none(self):
        assert normalize_boolean("maybe") is None
        assert normalize_boolean("yep") is None

    def test_numeric_truthy(self):
        assert normalize_boolean(1) is True
        assert normalize_boolean(42) is True
        assert normalize_boolean(0.5) is True

    def test_numeric_falsy(self):
        assert normalize_boolean(0) is False
        assert normalize_boolean(0.0) is False
