
"""MetroSpot API interface layer."""

from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4
from time import perf_counter
from datetime import datetime, timezone

import json
import logging
import sqlite3

from fastapi import Depends, FastAPI, Header, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from pydantic import AwareDatetime

from config import BASE_DIR

from models import (
    Login,
    Token,
    ReservationInput,
    Reservation,
    ReservationStatus,
    Spot,
    Error
)

from auth import issue_token, current_driver

import repository as repo
import service


# Initialize the database when the application starts
@asynccontextmanager
async def lifespan(app):

    repo.migrate()

    yield


app = FastAPI(
    title="MetroSpot API",
    version="0.3.0",
    lifespan=lifespan,
    debug=False
)

log = logging.getLogger("uvicorn.error")


# Authentication and idempotency dependencies
Driver = Annotated[str, Depends(current_driver)]

RequestKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=120,
        pattern=r"^[!-~]+$"
    )
]


# Document API error responses
def errors(*codes):

    return {
        code: {"model": Error}
        for code in codes
    }


# Return a consistent error response
def error_response(request, status, code, message):

    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "correlation_id": request.state.correlation_id
        },
        headers={"WWW-Authenticate": "Bearer"}
        if status == 401 else None
    )


# Correlation IDs and structured request logging
@app.middleware("http")
async def request_context(request, call_next):

    correlation_id = str(uuid4())

    request.state.correlation_id = correlation_id

    started = perf_counter()

    try:

        response = await call_next(request)

    except Exception:

        log.exception(
            "Unexpected error for request %s",
            correlation_id
        )

        response = error_response(
            request,
            500,
            "internal_error",
            "An unexpected error occurred."
        )

    response.headers["X-Correlation-ID"] = correlation_id

    log.info(json.dumps({
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "component": "metrospot_api",
        "event": "request_completed",
        "correlation_id": correlation_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round(
            (perf_counter() - started) * 1000, 2
        )
    }))

    return response


# Handle business rule violations
@app.exception_handler(service.ServiceError)
async def service_error(request: Request, exc):

    return error_response(
        request,
        exc.status,
        exc.code,
        str(exc)
    )


# Handle invalid request data
@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc):

    fields = ", ".join(sorted({
        str(item["loc"][-1])
        for item in exc.errors()
    }))

    return error_response(
        request,
        400,
        "invalid_request",
        f"Check these request fields: {fields}."
    )


# Handle database failures
@app.exception_handler(sqlite3.Error)
async def database_error(request: Request, exc):

    log.error(
        "Database error for request %s (%s)",
        request.state.correlation_id,
        type(exc).__name__
    )

    return error_response(
        request,
        503,
        "database_unavailable",
        "The database request could not be completed."
    )


# Front end
@app.get("/", include_in_schema=False)
def homepage():

    return FileResponse(BASE_DIR / "index.html")


# Liveness check
@app.get("/health/live")
def live():

    return {"status": "ok"}


# Readiness check
@app.get("/health/ready", responses=errors(503))
def ready():

    repo.check_readiness()

    return {"status": "ready"}


# Login
@app.post(
    "/auth/token",
    response_model=Token,
    responses=errors(400, 401)
)
def login(request: Login):

    return Token(
        access_token=issue_token(
            request.username,
            request.password
        )
    )


# Retrieve available parking spots
@app.get(
    "/spots",
    response_model=list[Spot],
    responses=errors(400, 401, 503)
)
def spots(
    driver: Driver,
    starts_at: AwareDatetime,
    ends_at: AwareDatetime
):

    return service.list_spots(
        starts_at,
        ends_at
    )


# Submit a reservation for asynchronous payment
@app.post(
    "/reservations",
    status_code=202,
    response_model=Reservation,
    responses=errors(400, 401, 404, 409, 503)
)
def reserve(
    request: ReservationInput,
    driver: Driver,
    request_key: RequestKey,
    http_request: Request
):

    return service.create_reservation(
        driver_id=driver,
        request_key=request_key,
        request=request,
        correlation_id=http_request.state.correlation_id
    )


# Retrieve the authenticated driver's reservations
# Version 0.3.0 introduces an optional status filter
@app.get(
    "/me/reservations",
    response_model=list[Reservation],
    responses=errors(400, 401, 503)
)
def my_reservations(
    driver: Driver,
    status: Annotated[
        ReservationStatus | None,
        Query(
            description=(
                "Optional reservation status filter. "
                "If omitted, all reservations belonging "
                "to the authenticated driver are returned."
            )
        )
    ] = None
):

    return service.list_reservations(
        driver,
        status
    )


# Retrieve a specific reservation and its payment status
@app.get(
    "/reservations/{reservation_id}",
    response_model=Reservation,
    responses=errors(400, 401, 404, 503)
)
def reservation(
    reservation_id: UUID,
    driver: Driver
):

    return service.get_reservation(
        driver,
        reservation_id
    )


# Preserve FastAPI's original OpenAPI generator
default_openapi = app.openapi


# Publish the extended OpenAPI contract
def published_openapi():

    contract_path = BASE_DIR / "openapi.json"

    # Use the exported contract when available
    if contract_path.exists():

        with open(contract_path, "r") as file:

            return json.load(file)

    # Otherwise, generate the contract from the API
    schema = default_openapi()

    # Validation errors return HTTP 400 instead of 422
    for path in schema["paths"].values():

        for operation in path.values():

            operation["responses"].pop("422", None)

    # Remove unused validation error schemas
    for name in (
        "HTTPValidationError",
        "ValidationError"
    ):

        schema.get("components", {}).get(
            "schemas", {}
        ).pop(name, None)

    return schema


app.openapi = published_openapi


# Start the application
if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000
    )
