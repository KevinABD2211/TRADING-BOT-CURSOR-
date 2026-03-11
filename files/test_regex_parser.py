"""
tests/unit/test_regex_parser.py
--------------------------------
Unit tests for the Stage 1 regex signal parser.

Tests cover:
  - Standard crypto futures signals
  - Stock signals
  - Options signals
  - Range entry signals
  - Multi-TP signals
  - Leverage extraction
  - Confidence scoring
  - Edge cases (malformed, empty, noise)
"""

from __future__ import annotations

import pytest
from app.services.signal_parser.regex_parser import RegexParser

# Instantiate once for all tests — it's stateless
parser = RegexParser()


class TestCryptoSignals:

    def test_standard_crypto_long(self):
        text = "BTC/USDT LONG | Entry: 42000 | SL: 41000 | TP1: 45000 TP2: 48000"
        result = parser.parse(text)
        assert result.symbol == "BTCUSDT"
        assert result.asset_type == "crypto"
        assert result.direction == "long"
        assert result.entry_price == 42000.0
        assert result.stop_loss == 41000.0
        assert result.take_profit_1 == 45000.0
        assert result.take_profit_2 == 48000.0
        assert result.confidence >= 0.8

    def test_crypto_short_with_leverage(self):
        text = "ETHUSDT SHORT @ 2100 sl 2200 tp 1900 10x"
        result = parser.parse(text)
        assert result.symbol == "ETHUSDT"
        assert result.direction == "short"
        assert result.entry_price == 2100.0
        assert result.stop_loss == 2200.0
        assert result.take_profit_1 == 1900.0
        assert result.leverage == 10
        assert result.confidence >= 0.75

    def test_crypto_entry_range(self):
        text = "#SOL LONG entry range 90-95, stoploss 86, targets 105/115/130"
        result = parser.parse(text)
        assert result.symbol == "SOL"
        assert result.direction == "long"
        assert result.entry_range_low == 90.0
        assert result.entry_range_high == 95.0
        assert result.stop_loss == 86.0
        assert result.take_profit_1 == 105.0

    def test_crypto_cashtag_symbol(self):
        text = "$BTC long entry 42500 sl 41800 tp 44000 tp2 46000"
        result = parser.parse(text)
        assert result.symbol == "BTC"
        assert result.direction == "long"
        assert result.confidence > 0.5

    def test_messy_crypto_signal(self):
        text = "bnb/usdt going long. entry 280. stop at 270. tp 295 300 310"
        result = parser.parse(text)
        assert result.symbol == "BNBUSDT"
        assert result.direction == "long"
        assert result.entry_price == 280.0
        assert result.stop_loss == 270.0
        assert result.take_profit_1 == 295.0

    def test_perp_notation(self):
        text = "SOLUSDT PERP | BUY | Entry 95 | SL 90 | Target 110"
        result = parser.parse(text)
        assert "SOL" in result.symbol
        assert result.direction == "long"


class TestStockSignals:

    def test_standard_stock_buy(self):
        text = "$AAPL BUY Entry 187.50 Stop 184.00 Target 195.00"
        result = parser.parse(text)
        assert result.symbol == "AAPL"
        assert result.asset_type == "stock"
        assert result.direction == "long"
        assert result.entry_price == 187.50
        assert result.stop_loss == 184.00
        assert result.take_profit_1 == 195.00

    def test_stock_short(self):
        text = "$TSLA SHORT entry 250.00 sl 262.00 tp 230.00 220.00"
        result = parser.parse(text)
        assert result.symbol == "TSLA"
        assert result.direction == "short"
        assert result.stop_loss == 262.00

    def test_stock_with_timeframe(self):
        text = "$NVDA LONG | Entry: 600 | SL: 585 | TP: 630 | TF: 4H"
        result = parser.parse(text)
        assert result.symbol == "NVDA"
        assert result.timeframe in ("4H", "4HR")

    def test_stock_intraday(self):
        text = "$SPY buy entry 470 stop 465 target 480 daily chart"
        result = parser.parse(text)
        assert result.symbol == "SPY"
        assert result.direction == "long"


class TestOptionsSignals:

    def test_options_call(self):
        text = "NVDA 600C 05/17 entry 15.00 sl 10.00 tp 25.00"
        result = parser.parse(text)
        assert result.symbol == "NVDA"
        assert result.asset_type == "option"
        assert result.options_type == "CALL"
        assert result.options_strike == 600.0

    def test_options_put(self):
        text = "SPY 400P entry 8.50 stop 5.00 target 18.00"
        result = parser.parse(text)
        assert result.asset_type == "option"
        assert result.options_type == "PUT"
        assert result.options_strike == 400.0


class TestEntryExtraction:

    def test_at_sign_entry(self):
        text = "BTCUSDT LONG @43000 SL 41500 TP 46000"
        result = parser.parse(text)
        assert result.entry_price == 43000.0

    def test_entry_with_colon(self):
        text = "ETH SHORT Entry: 2500 SL: 2600 TP: 2300"
        result = parser.parse(text)
        assert result.entry_price == 2500.0

    def test_entry_range_dash(self):
        text = "BTC LONG entry 42000-43000 sl 41000 tp 46000"
        result = parser.parse(text)
        assert result.entry_range_low == 42000.0
        assert result.entry_range_high == 43000.0
        assert result.entry_price is None

    def test_entry_range_slash(self):
        text = "ETHUSDT buy entry 2100/2150 stoploss 2000 target 2400"
        result = parser.parse(text)
        assert result.entry_range_low == 2100.0
        assert result.entry_range_high == 2150.0


class TestMultipleTP:

    def test_tp_slash_separated(self):
        text = "BTCUSDT LONG entry 42000 sl 41000 TP: 45000/48000/52000"
        result = parser.parse(text)
        assert result.take_profit_1 == 45000.0
        assert result.take_profit_2 == 48000.0
        assert result.take_profit_3 == 52000.0

    def test_tp_pipe_separated(self):
        text = "ETH LONG entry 2100 sl 2000 tp 2300 | 2500 | 2800"
        result = parser.parse(text)
        assert result.take_profit_1 == 2300.0

    def test_labeled_tps(self):
        text = "SOL LONG entry 90 sl 85 TP1 100 TP2 115 TP3 130"
        result = parser.parse(text)
        assert result.take_profit_1 == 100.0
        assert result.take_profit_2 == 115.0
        assert result.take_profit_3 == 130.0


class TestLeverage:

    def test_leverage_x_suffix(self):
        text = "BTCUSDT LONG entry 42000 sl 41000 tp 46000 25x"
        result = parser.parse(text)
        assert result.leverage == 25

    def test_leverage_x_prefix(self):
        text = "ETH SHORT x10 entry 2000 sl 2100 tp 1800"
        result = parser.parse(text)
        assert result.leverage == 10

    def test_leverage_word(self):
        text = "BTC LONG entry 43000 sl 42000 tp 46000 leverage 5x"
        result = parser.parse(text)
        assert result.leverage == 5

    def test_invalid_leverage_rejected(self):
        text = "BTC LONG entry 43000 sl 42000 tp 46000 999x"
        result = parser.parse(text)
        assert result.leverage is None  # 999 > 125 — rejected


class TestConfidenceScoring:

    def test_full_signal_high_confidence(self):
        text = "BTCUSDT LONG Entry 42000 SL 41000 TP 45000"
        result = parser.parse(text)
        assert result.confidence >= 0.8

    def test_direction_only_low_confidence(self):
        text = "looking bullish today"
        result = parser.parse(text)
        assert result.confidence < 0.5

    def test_no_stop_loss_penalised(self):
        text = "BTCUSDT LONG Entry 42000 TP 45000"
        result = parser.parse(text)
        assert result.confidence <= 0.65

    def test_empty_text_zero_confidence(self):
        result = parser.parse("")
        assert result.confidence == 0.0
        assert result.symbol is None

    def test_noise_message_zero_confidence(self):
        text = "gm everyone! bullish on crypto today 🚀"
        result = parser.parse(text)
        # No actionable entry/SL/TP data
        assert result.stop_loss is None
        assert result.entry_price is None


class TestEdgeCases:

    def test_unicode_dash_entry_range(self):
        """Em-dash in entry range: '42000 – 43000'"""
        text = "BTCUSDT LONG entry 42000 – 43000 sl 41000 tp 46000"
        result = parser.parse(text)
        # Should extract at least the entry as single or parse robustly
        assert result.direction == "long"

    def test_comma_decimal_price(self):
        """European-style comma decimal: '42,000.50'"""
        text = "BTCUSDT LONG entry 42,000 sl 41,000 tp 45,000"
        result = parser.parse(text)
        assert result.entry_price == 42000.0
        assert result.stop_loss == 41000.0

    def test_very_long_message(self):
        """Parser should not crash on very long messages."""
        long_text = "BTC LONG entry 42000 sl 41000 tp 46000 " + ("analysis " * 500)
        result = parser.parse(long_text)
        assert result.symbol is not None

    def test_multiple_symbols_takes_first(self):
        """When multiple symbols appear, should take the dominant one."""
        text = "BTCUSDT vs ETHUSDT LONG entry 42000 sl 41000 tp 46000"
        result = parser.parse(text)
        # Should pick one symbol and not crash
        assert result.symbol is not None

    def test_lowercase_signal(self):
        text = "btcusdt long entry 42000 sl 41000 tp 46000"
        result = parser.parse(text)
        assert result.symbol == "BTCUSDT"
        assert result.direction == "long"
