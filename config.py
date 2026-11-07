"""
Configuration constants and environment settings.
Conforms to SYSTEM_DESIGN.md Section 3.1 and PRD.md Section 5.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base workspace directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / ".env")

# BingX API settings
BINGX_BASE_URL = os.getenv("BINGX_BASE_URL", "https://open-api.bingx.pro").rstrip("/")
BINGX_API_KEY = os.getenv("BINGX_API_KEY", "").strip()
BINGX_API_SECRET = os.getenv("BINGX_API_SECRET", "").strip()

# Trading cutoff: 2026-09-14 00:00:00 IST (UTC+05:30)
# 2026-09-13 18:30:00 UTC = 1789324200000 ms
CUTOFF_DATE = "2026-09-14"
CUTOFF_EPOCH_MS = 1789324200000

# Retention and windowing constants (PRD §5.1, §7.1)
HISTORY_DAYS = 89
WINDOW_MS = 7 * 24 * 3600 * 1000  # 604_800_000 ms
RATE_LIMIT_SLEEP = 0.3  # seconds between windowed calls (5 req/s limit)

# Display timezone
TIMEZONE = "Asia/Kolkata"

# Database configuration
SQLITE_PATH = os.getenv("SQLITE_PATH", str(BASE_DIR / "trades.db"))
SCHEMA_PATH = BASE_DIR / "schema.sql"
SCHEMA_VERSION = 1

# API request window
REC_WINDOW_MS = 5000

