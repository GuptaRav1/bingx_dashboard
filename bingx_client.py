"""
BingX OpenAPI HTTP Client.
Conforms to SYSTEM_DESIGN.md Section 7, Section 14.1, and PRD.md Section 5.
"""

import time
import logging
from urllib.parse import urlencode
from typing import Any
import requests

import config
from signer import sign

logger = logging.getLogger(__name__)


class BingxError(Exception):
    """Raised when BingX returns a non-zero error code or API error."""
    pass


def now_ms() -> int:
    """Returns current epoch time in milliseconds."""
    return int(time.time() * 1000)


def _signed_get(path: str, params: dict[str, str]) -> Any:
    """
    Executes a signed GET request against BingX OpenAPI.
    
    Invariants (SYSTEM_DESIGN.md Section 7):
    - timestamp is merged and signed as part of the query params.
    - URL query parameters are formatted in alphabetical order to match the canonical signature.
    - Retry once on requests.RequestException or HTTP 5xx with 1s backoff.
    - Do not retry on HTTP 4xx or code != 0.
    - Never log secrets; log at most first 8 characters of signature (Section 14.1).
    """
    if not config.BINGX_API_KEY or not config.BINGX_API_SECRET:
        raise BingxError("Missing BINGX_API_KEY or BINGX_API_SECRET in configuration.")

    params_with_ts = {**params, "timestamp": str(now_ms())}
    sig = sign(params_with_ts, secret=config.BINGX_API_SECRET)

    # BingX verifies against the raw query string in transport; keep sorted order
    sorted_items = sorted(params_with_ts.items())
    query_str = urlencode(sorted_items)
    url = f"{config.BINGX_BASE_URL}{path}?{query_str}&signature={sig}"

    headers = {
        "X-BX-APIKEY": config.BINGX_API_KEY,
    }

    masked_sig = sig[:8] + "..." if len(sig) > 8 else "***"
    logger.debug(f"GET {path}?{query_str}&signature={masked_sig}")

    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=15)

            # Retry once on 5xx
            if 500 <= r.status_code < 600:
                logger.warning(f"BingX 5xx server error (status {r.status_code}) on attempt {attempt + 1}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                r.raise_for_status()

            # Fail immediately on 4xx without retry
            r.raise_for_status()

            body = r.json()
            code = body.get("code")
            if code != 0:
                msg = body.get("msg", "")
                raise BingxError(f"code={code} msg={msg}")

            return body.get("data")

        except requests.RequestException as e:
            logger.warning(f"BingX RequestException on attempt {attempt + 1}: {e}")
            if attempt == 0:
                time.sleep(1)
                continue
            raise


def fetch_orders_window(start_ms: int, end_ms: int) -> list[dict]:
    """
    Fetches one window of historical orders (<= 7 days).
    PRD Section 5.1:
    - Path: /openApi/swap/v2/trade/allOrders
    - Params: startTime, endTime, limit=1000, timestamp
    - symbol is omitted to return orders across all symbols
    """
    path = "/openApi/swap/v2/trade/allOrders"
    params = {
        "startTime": str(start_ms),
        "endTime": str(end_ms),
        "limit": "1000",
    }
    data = _signed_get(path, params)
    if isinstance(data, dict):
        return data.get("orders", [])
    return []


def fetch_balance() -> dict:
    """
    Fetches user's perpetual account balance.
    PRD Section 5.2 & SYSTEM_DESIGN.md Section 5.3:
    - Path: /openApi/swap/v3/user/balance
    - No params except timestamp.
    - Response data is an array; picks asset == 'USDT'.
    - Returns dict with asset, balance, equity, unrealized_profit, and snapshot timestamp.
    """
    path = "/openApi/swap/v3/user/balance"
    data = _signed_get(path, {})

    if not isinstance(data, list):
        raise BingxError(f"Expected list response for balance endpoint, got {type(data).__name__}")

    usdt_entry = next((item for item in data if item.get("asset") == "USDT"), None)
    if not usdt_entry:
        raise BingxError("USDT asset not found in BingX balance response.")

    return {
        "asset": "USDT",
        "balance": float(usdt_entry.get("balance", 0.0)),
        "equity": float(usdt_entry.get("equity", 0.0)),
        "unrealized_profit": float(usdt_entry.get("unrealizedProfit", 0.0)),
        "ts": now_ms(),
    }

