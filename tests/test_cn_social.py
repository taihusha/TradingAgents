"""Tests for Chinese social media data fetchers (cn_social.py)."""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from tradingagents.dataflows import cn_social


# ---------------------------------------------------------------------------
# Ticker normalisation
# ---------------------------------------------------------------------------

class TestNormalizeCnTicker:
    def test_sz_ticker_with_suffix(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("000933.SZ")
        assert bare == "000933"
        assert exchange == "SZ"
        assert symbol == "SZ000933"

    def test_ss_ticker_with_suffix(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("600111.SS")
        assert bare == "600111"
        assert exchange == "SH"
        assert symbol == "SH600111"

    def test_sz_ticker_bare(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("000725")
        assert bare == "000725"
        assert exchange == "SZ"

    def test_ss_ticker_bare(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("600519")
        assert bare == "600519"
        assert exchange == "SH"

    def test_non_cn_ticker(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("AAPL")
        assert bare == ""
        assert exchange == ""
        assert symbol == ""

    def test_case_insensitive(self):
        bare, exchange, symbol = cn_social._normalize_cn_ticker("000933.sz")
        assert bare == "000933"
        assert exchange == "SZ"


# ---------------------------------------------------------------------------
# Safe HTTP fetch
# ---------------------------------------------------------------------------

class TestSafeHttpFetch:
    def test_http_error_returns_error_tuple(self):
        err = HTTPError("url", 503, "Unavailable", {}, None)
        with patch.object(cn_social, "urlopen", side_effect=err):
            text, error = cn_social._safe_http_fetch("http://test.url")
        assert text is None
        assert "HTTPError" in error

    def test_timeout_returns_error_tuple(self):
        with patch.object(cn_social, "urlopen", side_effect=TimeoutError("timed out")):
            text, error = cn_social._safe_http_fetch("http://test.url")
        assert text is None
        assert "TimeoutError" in error


# ---------------------------------------------------------------------------
# Eastmoney Guba
# ---------------------------------------------------------------------------

class TestEastmoneyGuba:
    def test_non_cn_ticker_returns_placeholder(self):
        result = cn_social.fetch_eastmoney_guba_posts("AAPL")
        assert "does not appear to be a Chinese" in result

    def test_api_and_html_both_fail_gracefully(self):
        """When both API and HTML fail, returns a clear placeholder."""
        with patch.object(
            cn_social, "_safe_http_fetch", return_value=(None, "Connection refused")
        ):
            result = cn_social.fetch_eastmoney_guba_posts("000933.SZ")
        assert "no Eastmoney Guba posts found" in result

    def test_json_api_parse(self):
        """Verify _parse_guba_json handles a valid API response."""
        data = {
            "result": [
                {
                    "title": "神火股份看好",
                    "user_name": "散户甲",
                    "post_time": "2026-06-12 14:30",
                    "pz": "1",
                    "reply_count": 5,
                }
            ]
        }
        posts = cn_social._parse_guba_json(data, limit=10)
        assert len(posts) == 1
        assert posts[0]["title"] == "神火股份看好"
        assert posts[0]["sentiment"] == "看多"


# ---------------------------------------------------------------------------
# Xueqiu
# ---------------------------------------------------------------------------

class TestXueqiu:
    def test_non_cn_ticker_returns_placeholder(self):
        result = cn_social.fetch_xueqiu_posts("NVDA")
        assert "does not appear to be a Chinese" in result


# ---------------------------------------------------------------------------
# 10jqka
# ---------------------------------------------------------------------------

class Test10jqka:
    def test_non_cn_ticker_returns_placeholder(self):
        result = cn_social.fetch_10jqka_posts("TSLA")
        assert "does not appear to be a Chinese" in result

    def test_all_urls_fail_gracefully(self):
        with patch.object(
            cn_social, "_safe_http_fetch", return_value=(None, "404 Not Found")
        ):
            result = cn_social.fetch_10jqka_posts("600111.SS")
        assert "no 10jqka posts found" in result


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

class TestFetchCnSocialPosts:
    def test_empty_sources_returns_empty_string(self):
        result = cn_social.fetch_cn_social_posts("000933.SZ", sources=[])
        assert result == ""

    def test_unknown_source_ignored(self):
        result = cn_social.fetch_cn_social_posts(
            "000933.SZ", sources=["nonexistent_source"]
        )
        assert result == ""

    def test_multiple_sources_concatenated(self):
        with patch.object(
            cn_social, "fetch_eastmoney_guba_posts", return_value="Guba data"
        ), patch.object(
            cn_social, "fetch_xueqiu_posts", return_value="Xueqiu data"
        ):
            result = cn_social.fetch_cn_social_posts(
                "000933.SZ", sources=["eastmoney_guba", "xueqiu"]
            )
        assert "Guba data" in result
        assert "Xueqiu data" in result
        assert "---" in result

    def test_fetcher_exception_returns_placeholder(self):
        """When a fetcher raises, it returns a placeholder with 'error' in it."""
        with patch.object(
            cn_social,
            "_SOURCE_FETCHERS",
            {
                "eastmoney_guba": lambda *a, **k: (_ for _ in ()).throw(
                    RuntimeError("unexpected")
                )
            },
        ):
            result = cn_social.fetch_cn_social_posts(
                "000933.SZ", sources=["eastmoney_guba"]
            )
        assert "error" in result.lower()
