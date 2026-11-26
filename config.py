
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
