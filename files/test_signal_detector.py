"""
tests/unit/test_signal_detector.py
-------------------------------------
Unit tests for the SignalDetector heuristic pre-filter.
"""

from __future__ import annotations

import pytest
from app.services.signal_detector import SignalDetector

detector = SignalDetector()


class TestSignalDetection:

    def test_strong_signal_detected(self):
        text = "BTC/USDT LONG | Entry: 42000 | SL: 41000 | TP: 45000"
        is_signal, confidence, keywords = detector.detect(text)
        assert is_signal is True
        assert confidence >= 0.6
        assert any("ticker" in kw for kw in keywords)
        assert any("direction" in kw for kw in keywords)

    def test_cashtag_signal_detected(self):
        text = "$AAPL BUY Entry 187 Stop 184 Target 195"
        is_signal, confidence, _ = detector.detect(text)
        assert is_signal is True

    def test_clear_noise_rejected(self):
        is_signal, confidence, _ = detector.detect("gm everyone!")
        assert is_signal is False

    def test_single_emoji_rejected(self):
        is_signal, _, _ = detector.detect("🚀")
        assert is_signal is False

    def test_pure_link_rejected(self):
        is_signal, _, _ = detector.detect("https://example.com/article")
        assert is_signal is False

    def test_empty_string_rejected(self):
        is_signal, confidence, _ = detector.detect("")
        assert is_signal is False
        assert confidence == 0.0

    def test_short_bullish_without_levels(self):
        """'looking bullish' alone should not be classified as signal."""
        is_signal, confidence, _ = detector.detect("ETH looking bullish today")
        # Low confidence — direction keyword present but no price levels
        assert confidence < 0.5

    def test_leverage_increases_confidence(self):
        text = "BTCUSDT LONG 10x"
        result_with_lev = detector.evaluate(text)
        text_no_lev = "BTCUSDT LONG"
        result_no_lev = detector.evaluate(text_no_lev)
        assert result_with_lev.confidence >= result_no_lev.confidence

    def test_price_keywords_increase_confidence(self):
        text = "BTC LONG entry 42000 sl 41000 tp 46000"
        result = detector.evaluate(text)
        assert result.has_price_levels is True
        assert result.confidence > 0.7


class TestDetectionResult:

    def test_detected_ticker_is_captured(self):
        result = detector.evaluate("ETHUSDT LONG entry 2000 sl 1900 tp 2200")
        assert result.detected_ticker is not None
        assert "ETH" in result.detected_ticker.upper()

    def test_detected_direction_is_captured(self):
        result = detector.evaluate("BTCUSDT SHORT sl 44000 tp 40000")
        assert result.detected_direction == "short"

    def test_reason_is_populated(self):
        result = detector.evaluate("SOL LONG entry 90 sl 85 tp 100")
        assert result.reason
        assert "ticker" in result.reason
