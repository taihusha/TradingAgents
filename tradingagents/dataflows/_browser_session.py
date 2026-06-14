"""Browser TLS session injection for akshare / Eastmoney requests.

Eastmoney's CDN detects Python's default TLS fingerprint (JA3/JA4) and
progressively rate-limits or bans non-browser clients.  This module
monkey-patches ``requests.Session`` globally so that **every** akshare
call (and any other library using ``requests`` in this process) presents a
real browser TLS handshake via ``curl_cffi``.

Three tiers (stacked):
1. **UA header injection** (auto) — browser headers on every request.
   Handles basic UA filtering. Compatible with everything.
2. **Default timeout** (auto) — 15-second timeout on every request.
   Prevents TCP hangs when Eastmoney silently black-holes connections
   from banned IPs (the OS default is 60-120s per dead connection).
3. **curl_cffi TLS replacement** (opt-in via ``enable_curl_cffi()``) —
   real browser TLS fingerprint (Chrome 110), but may be incompatible
   with akshare's ``mount()`` + ``params`` request pattern.

Circuit breaker
---------------
``record_akshare_failure()`` / ``is_akshare_blocked()`` let the vendor
layer skip akshare entirely after N consecutive failures within one
process lifetime.  Call ``is_akshare_blocked()`` before attempting an
akshare call; call ``record_akshare_failure()`` when one fails.

Import this module early (before any akshare import) to apply the patches.
Safe to import multiple times — the patches run at most once.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_patch_applied = False

_DEFAULT_TIMEOUT = 15  # seconds — cuts TCP hang from OS-default 60-120s

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/",
}

# ── Circuit breaker for akshare ──────────────────────────────────────────
# After _AKSHARE_BREAKER_THRESHOLD consecutive failures in one process
# lifetime, skip akshare entirely — every second spent waiting on a
# banned IP is a second the user could have had results from baostock.

_AKSHARE_FAILURE_COUNT = 0
_AKSHARE_BREAKER_THRESHOLD = 3


def record_akshare_failure():
    """Increment the akshare failure counter."""
    global _AKSHARE_FAILURE_COUNT
    _AKSHARE_FAILURE_COUNT += 1
    if _AKSHARE_FAILURE_COUNT >= _AKSHARE_BREAKER_THRESHOLD:
        logger.warning(
            "akshare circuit breaker OPEN — %d consecutive failures. "
            "Skipping akshare for remaining calls this run.",
            _AKSHARE_FAILURE_COUNT,
        )


def is_akshare_blocked() -> bool:
    """Return True when akshare should be skipped (too many failures)."""
    return _AKSHARE_FAILURE_COUNT >= _AKSHARE_BREAKER_THRESHOLD


def _reset_circuit_breaker():
    """Reset the breaker (exposed for testing only)."""
    global _AKSHARE_FAILURE_COUNT
    _AKSHARE_FAILURE_COUNT = 0


def _apply_curl_cffi_patch():
    """Replace ``requests.Session`` with curl_cffi's browser-TLS version."""
    try:
        import curl_cffi.requests as _curl_req  # type: ignore[import-untyped]
        import requests as _requests

        # Add mount() stub — akshare's request_with_retry calls
        # session.mount(...) but curl_cffi.Session doesn't have it.
        if not hasattr(_curl_req.Session, "mount"):
            _curl_req.Session.mount = lambda self, *a, **kw: None  # type: ignore[method-assign]

        # Set a default impersonate so existing akshare code that calls
        # Session().get(url, ...) without impersonate= still gets TLS protection.
        _original_init = _curl_req.Session.__init__

        def _patched_init(self, *args, **kwargs):
            kwargs.setdefault("impersonate", "chrome110")
            _original_init(self, *args, **kwargs)

        _curl_req.Session.__init__ = _patched_init  # type: ignore[method-assign]

        # Replace the class reference globally
        _requests.Session = _curl_req.Session  # type: ignore[assignment]
        logger.debug("TLS: curl_cffi.Session (Chrome 110) replacing requests.Session")
        return True
    except ImportError:
        logger.debug("TLS: curl_cffi not installed; falling back to UA header injection")
        return False
    except Exception as exc:
        logger.warning("TLS: curl_cffi patch failed (%s); falling back to UA headers", exc)
        return False


def _apply_ua_header_patch():
    """Inject browser headers + default timeout into every ``Session.request`` call.

    The timeout prevents TCP hangs when Eastmoney CDN silently drops
    connections from banned IPs (OS default = 60-120s per connection).
    With 6+ akshare calls per run, this saves minutes of dead waiting.
    """
    try:
        import requests as _requests

        _original_request = _requests.Session.request

        def _patched_request(self, method, url, **kwargs):
            # ── Timeout guard (prevent TCP-level hangs) ──
            # akshare never passes a timeout, so every Eastmoney call
            # would otherwise use the OS default (60-120s on Windows).
            # A single dead connection wastes up to 2 minutes; the
            # fundamentals analyst alone makes 6+ akshare calls.
            kwargs.setdefault("timeout", _DEFAULT_TIMEOUT)

            # ── Browser header injection ──
            headers = kwargs.get("headers")
            if headers is None:
                headers = {}
                kwargs["headers"] = headers
            if isinstance(headers, dict):
                for key, value in _BROWSER_HEADERS.items():
                    headers.setdefault(key, value)
            return _original_request(self, method, url, **kwargs)

        _requests.Session.request = _patched_request  # type: ignore[method-assign]
        logger.debug(
            "UA: browser headers + %ds timeout injected into requests.Session.request",
            _DEFAULT_TIMEOUT,
        )
        return True
    except Exception:
        return False


def inject_browser_session():
    """Apply the best available browser-session patch (one-shot).

    Strategy (in priority order):
    1. UA header injection + default timeout — always applied.  Handles
       basic UA filtering and prevents TCP hangs (15s timeout vs OS
       default 60-120s).  Fully compatible with akshare's usage patterns.
    2. curl_cffi TLS replacement — **opt-in via ``enable_curl_cffi()``**.
       Provides real browser TLS fingerprint (Chrome 110) for extra
       anti-detection, but may cause edge-case issues with akshare's
       ``mount()`` + ``params`` request pattern.

    Safe to call multiple times — the patch runs at most once per tier.
    """
    global _patch_applied
    if _patch_applied:
        return
    _patch_applied = True

    # Always apply UA header injection (compatible, reliable)
    _apply_ua_header_patch()

    # curl_cffi is NOT auto-applied — call enable_curl_cffi() explicitly
    # if you need TLS-level impersonation (e.g., behind strict WAFs).


def enable_curl_cffi():
    """Opt-in: replace ``requests.Session`` with curl_cffi's browser-TLS version.

    Call AFTER importing this module.  Not auto-applied because akshare's
    ``request_with_retry`` uses ``session.mount()`` which curl_cffi doesn't
    support, and some Eastmoney endpoints reject the curl_cffi connection
    pattern when query params are present.
    """
    _apply_curl_cffi_patch()


# ── Apply at import time ─────────────────────────────────────────────────────
# UA headers + 15s timeout are auto-applied; curl_cffi is opt-in only.
inject_browser_session()
