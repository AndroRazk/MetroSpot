
"""JWT authentication for MetroSpot."""

from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Annotated

import jwt

from fastapi import Depends
from fastapi.security import (
    HTTPBearer,
    HTTPAuthorizationCredentials
)

from config import (
    JWT_SECRET,
    JWT_ALGORITHM,
    JWT_ISSUER,
    JWT_EXPIRATION_HOURS,
    DRIVER_CREDENTIALS
)

from service import ServiceError


bearer = HTTPBearer(auto_error=False)


# Validate credentials and generate a JWT token
def issue_token(username, password):

    expected_password = DRIVER_CREDENTIALS.get(username)

    if expected_password is None or not compare_digest(
        expected_password.encode(),
        password.encode()
    ):
        raise ServiceError(
            401,
            "invalid_credentials",
            "The username or password is incorrect."
        )

    return jwt.encode(
        {
            "sub": username,
            "iss": JWT_ISSUER,
            "exp": datetime.now(timezone.utc)
            + timedelta(hours=JWT_EXPIRATION_HOURS)
        },
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )


# Validate a JWT token and identify the driver
def current_driver(
    auth: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer)
    ]
):

    if auth is not None:

        try:
            claims = jwt.decode(
                auth.credentials,
                JWT_SECRET,
                algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER,
                options={
                    "require": ["exp", "iss", "sub"]
                }
            )

            username = claims["sub"]

            if DRIVER_CREDENTIALS.get(username) is not None:
                return username

        except jwt.InvalidTokenError:
            pass

    raise ServiceError(
        401,
        "authentication_required",
        "Sign in with a valid bearer token."
    )
