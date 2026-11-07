"""
HMAC-SHA256 request signer for BingX OpenAPI.
Conforms to SYSTEM_DESIGN.md Section 7.
"""

import hmac
import hashlib


def sign(params: dict[str, str], secret: str) -> str:
    """
    params: query parameters EXCLUDING the signature itself.
            MUST include 'timestamp'.
    Returns: hex HMAC-SHA256 of the canonical query string.

    Signing invariants (SYSTEM_DESIGN.md Section 7):
    - timestamp is part of the signed string (BingX requires it).
    - Parameters are sorted alphabetically by key.
    - Values are joined as k=v without URL encoding during signing.
    """
    items = sorted(params.items())
    canonical = "&".join(f"{k}={v}" for k, v in items)
    return hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

