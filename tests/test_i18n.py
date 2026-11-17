"""i18n tests."""

from __future__ import annotations

from pairtner.i18n import _STRINGS, t


class TestI18n:
    def test_english_key(self):
        result = t("en", "welcome", code="ABC123")
        assert "ABC123" in result
        assert "pAIrtner" in result

    def test_telugu_key(self):
        result = t("te", "welcome", code="XYZ")
        assert "XYZ" in result
        # Should contain Telugu characters
        assert "స్వాగతం" in result

    def test_fallback_to_english(self):
        """Missing key in Telugu should fall back to English."""
        # Use a key that exists in both — but test with a fake lang
        result = t("xx", "welcome", code="TEST")
        assert "TEST" in result
        assert "pAIrtner" in result

    def test_missing_key(self):
        """Completely missing key returns the key itself."""
        result = t("en", "nonexistent_key_xyz")
        assert result == "nonexistent_key_xyz"

    def test_format_kwargs(self):
        result = t("en", "join_success", partner_name="Bob")
        assert "Bob" in result

    def test_crisis_resources(self):
        result = t("en", "crisis_resources")
        assert "9152987821" in result  # iCall
        assert "1860-2662-345" in result  # Vandrevala

    def test_help_text(self):
        result = t("en", "help")
        assert "/start" in result
        assert "/tell" in result
        assert "/mediate" in result

    def test_button_labels(self):
        assert "Approve" in t("en", "btn_approve")
        assert "Edit" in t("en", "btn_edit") or "✏️" in t("en", "btn_edit")

    def test_telugu_buttons(self):
        assert "ఆమోదించు" in t("te", "btn_approve")
        assert "మార్చు" in t("te", "btn_edit")

    def test_not_therapy_present(self):
        result = t("en", "not_therapy")
        assert "not" in result.lower()
        assert "therapy" in result.lower()

    def test_privacy_disclaimer_en(self):
        # not_therapy is split: the privacy sentence is now privacy_note.
        result = t("en", "privacy_note", transport="Telegram servers", llm="Google Gemini")
        assert "telegram" in result.lower()
        assert "gemini" in result.lower()
        assert "stored locally" in result.lower()

    def test_privacy_disclaimer_te(self):
        result = t("te", "privacy_note", transport="Telegram servers", llm="Google Gemini")
        assert "Telegram" in result
        assert "Gemini" in result


# ---------------------------------------------------------------------------
# FEAT-005: en/te key parity
# ---------------------------------------------------------------------------


def test_en_te_key_parity():
    """Every i18n key must exist in both 'en' and 'te' string tables."""
    en_keys = set(_STRINGS["en"].keys())
    te_keys = set(_STRINGS["te"].keys())

    missing_in_te = en_keys - te_keys
    missing_in_en = te_keys - en_keys

    assert not missing_in_te, f"Keys in 'en' missing from 'te': {sorted(missing_in_te)}"
    assert not missing_in_en, f"Keys in 'te' missing from 'en': {sorted(missing_in_en)}"
