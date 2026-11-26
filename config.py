
"""Environment configuration for MetroSpot."""

import os
from pathlib import Path
from secrets import token_hex


# Project directory
BASE_DIR = Path(__file__).resolve().parent


# Database location
DB_PATH = Path(
    os.getenv("DB_PATH", str(BASE_DIR / "metrospot.sqlite3"))
)


# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET") or token_hex(32)
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "metrospot"
JWT_EXPIRATION_HOURS = 1


# Validate the JWT secret
if len(JWT_SECRET.encode()) < 32:
    raise RuntimeError(
        "JWT_SECRET must contain at least 32 bytes."
    )


# Driver credentials
DRIVER_CREDENTIALS = {
    "driver-a": os.getenv("DRIVER_A_PASSWORD"),
    "driver-b": os.getenv("DRIVER_B_PASSWORD"),
}


# Parking configuration
HOURLY_RATE_CENTS = 500

HOLD_TTL_SECONDS = int(
    os.getenv("HOLD_TTL_SECONDS", "120")
)


# Payment provider configuration
PAYMENT_PROVIDER_URL = os.getenv(
    "PAYMENT_PROVIDER_URL",
    "http://127.0.0.1:8001"
)

PAYMENT_TIMEOUT_SECONDS = 3

PAYMENT_MAX_ATTEMPTS = 3

PAYMENT_RETRY_BASE_SECONDS = 2

PAYMENT_LEASE_SECONDS = 30


# Background worker configuration
WORKER_POLL_SECONDS = 1
