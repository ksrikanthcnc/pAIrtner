"""i18n tests — string catalog (English only)."""

from __future__ import annotations

from pairtner.i18n import _STRINGS, t


class TestI18n:
    def test_english_key(self):
        result = t("en", "welcome", code="ABC123")
        assert "ABC123" in result
        assert "pAIrtner" in result

    def test_fallback_to_english(self):
        """Unknown lang falls back to English."""
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

    def test_not_therapy_present(self):
        result = t("en", "not_therapy")
        assert "not" in result.lower()
        assert "therapy" in result.lower()

    def test_privacy_disclaimer(self):
        result = t("en", "privacy_note", transport="Telegram servers", llm="Google Gemini")
        assert "telegram" in result.lower()
        assert "gemini" in result.lower()
        assert "stored locally" in result.lower()
