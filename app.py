"""MetroSpot milestone API, booking rules and SQLite access."""
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
from secrets import token_hex
import sqlite3
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints

SpotID = Annotated[str, StringConstraints(pattern=r"^P-[0-9]{3}$")]
DriverName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Plate = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{0,15}$")]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Literal["driver-a", "driver-b"]
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class Token(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class ReservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spot_id: SpotID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    driver_name: DriverName
    license_plate: Plate


class Reservation(BaseModel):
    id: UUID
    spot_id: SpotID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal["confirmed"] = "confirmed"


class Spot(BaseModel):
    id: SpotID


class Error(BaseModel):
    code: str
    message: str
    correlation_id: str


DB_PATH = Path(__file__).with_name("metrospot.sqlite3")


@contextmanager
def database(*, write=False):
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as connection:
        connection.row_factory = lambda cursor, row: dict(zip(
            (column[0] for column in cursor.description), row
        ))
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            if write:
                # Acquire the write lock before any availability checks.
                connection.execute("BEGIN IMMEDIATE")
            yield connection


def migrate():
    with database(write=True) as db:
        version = next(iter(db.execute("PRAGMA user_version").fetchone().values()))
        if version < 1:
            db.execute("CREATE TABLE spots (id TEXT PRIMARY KEY, occupied INTEGER NOT NULL DEFAULT 0)")
            db.executemany("INSERT INTO spots (id) VALUES (?)", [(f"P-{i:03}",) for i in range(1, 201)])
            db.execute("""CREATE TABLE reservations (
                id TEXT PRIMARY KEY, driver_id TEXT NOT NULL,
                spot_id TEXT NOT NULL REFERENCES spots(id),
                starts_at TEXT NOT NULL, ends_at TEXT NOT NULL CHECK (ends_at > starts_at),
                driver_name TEXT NOT NULL, license_plate TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX reservations_spot_time ON reservations (spot_id, starts_at, ends_at)")
            db.execute("PRAGMA user_version = 1")
        if version < 2:
            db.execute("""CREATE TABLE reservation_requests (
                driver_id TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                reservation_id TEXT NOT NULL REFERENCES reservations(id),
                PRIMARY KEY (driver_id, request_key)
            )""")
            db.execute("PRAGMA user_version = 2")


def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def find_request(db, driver, key):
    saved = db.execute(
        "SELECT fingerprint, reservation_id FROM reservation_requests WHERE driver_id = ? AND request_key = ?",
        (driver, key),
    ).fetchone()
    return saved


def save_reservation(db, request, driver, key, fingerprint, reservation_id):
    db.execute(
        "INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (reservation_id, driver, request.spot_id, timestamp(request.starts_at),
         timestamp(request.ends_at), request.driver_name, request.license_plate),
    )
    db.execute("INSERT INTO reservation_requests VALUES (?, ?, ?, ?)",
               (driver, key, fingerprint, reservation_id))
    return owned_reservation(db, reservation_id, driver)


def owned_reservation(db, reservation_id, driver):
    return db.execute(
        "SELECT id, spot_id, starts_at, ends_at FROM reservations WHERE id = ? AND driver_id = ?",
        (reservation_id, driver),
    ).fetchone()


def driver_reservations(db, driver):
    return db.execute(
        "SELECT id, spot_id, starts_at, ends_at FROM reservations WHERE driver_id = ? ORDER BY starts_at DESC",
        (driver,),
    ).fetchall()


class ServiceError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


def validate_window(start, end):
    if end <= start:
        raise ServiceError(400, "invalid_window", "Departure must be after arrival.")


def list_spots(start, end):
    validate_window(start, end)
    with database() as db:
        return db.execute(
            """SELECT id FROM spots WHERE NOT occupied AND NOT EXISTS (
                SELECT 1 FROM reservations WHERE spot_id = spots.id
                AND starts_at < ? AND ends_at > ?
            ) ORDER BY id""",
            (timestamp(end), timestamp(start)),
        ).fetchall()


def create_reservation(driver_id, request_key, request):
    # Normalize equivalent offsets so a retry can represent the same instant in UTC.
    request = request.model_copy(update={
        "starts_at": request.starts_at.astimezone(timezone.utc),
        "ends_at": request.ends_at.astimezone(timezone.utc),
    })
    fingerprint = sha256(request.model_dump_json().encode()).hexdigest()

    with database(write=True) as db:
        saved = find_request(db, driver_id, request_key)
        if saved and saved["fingerprint"] != fingerprint:
            raise ServiceError(409, "idempotency_conflict", "This key belongs to a different booking request.")
        if saved:
            return owned_reservation(db, saved["reservation_id"], driver_id)

        validate_window(request.starts_at, request.ends_at)
        if request.starts_at < datetime.now(timezone.utc):
            raise ServiceError(400, "past_arrival", "Choose an arrival time in the future.")

        spot = db.execute("SELECT occupied FROM spots WHERE id = ?", (request.spot_id,)).fetchone()
        if spot is None:
            raise ServiceError(404, "spot_not_found", "This spot does not exist.")
        overlap = db.execute(
            "SELECT 1 FROM reservations WHERE spot_id = ? AND starts_at < ? AND ends_at > ? LIMIT 1",
            (request.spot_id, timestamp(request.ends_at), timestamp(request.starts_at)),
        ).fetchone()
        if spot["occupied"] or overlap:
            raise ServiceError(409, "spot_unavailable", "This spot is unavailable for the requested time.")
        return save_reservation(db, request, driver_id, request_key, fingerprint, str(uuid4()))


def get_reservation(driver_id, reservation_id):
    with database() as db:
        row = owned_reservation(db, str(reservation_id), driver_id)
    if row is None:
        raise ServiceError(404, "reservation_not_found", "Reservation not found.")
    return row


def list_reservations(driver_id):
    with database() as db:
        return driver_reservations(db, driver_id)

secret = os.getenv("JWT_SECRET") or token_hex(32)
if len(secret.encode()) < 32:
    raise RuntimeError("JWT_SECRET must contain at least 32 bytes.")

credentials = {
    "driver-a": os.getenv("DRIVER_A_PASSWORD", "demo-a"),
    "driver-b": os.getenv("DRIVER_B_PASSWORD", "demo-b"),
}
bearer = HTTPBearer(auto_error=False)


def issue_token(username, password):
    if not compare_digest(credentials[username].encode(), password.encode()):
        raise ServiceError(401, "invalid_credentials", "The username or password is incorrect.")
    return jwt.encode(
        {"sub": username, "iss": "metrospot", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        secret,
        algorithm="HS256",
    )


def current_driver(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if auth is not None:
        try:
            claims = jwt.decode(auth.credentials, secret, algorithms=["HS256"], issuer="metrospot",
                                options={"require": ["exp", "iss", "sub"]})
            if claims["sub"] in credentials:
                return claims["sub"]
        except jwt.InvalidTokenError:
            pass
    raise ServiceError(401, "authentication_required", "Sign in with a valid bearer token.")


@asynccontextmanager
async def lifespan(app):
    migrate()
    yield


app = FastAPI(title="MetroSpot API", version="0.1.0", lifespan=lifespan)

log = logging.getLogger("uvicorn.error")

Driver = Annotated[str, Depends(current_driver)]
RequestKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=120, pattern=r"^[!-~]+$")]


def errors(*codes):
    return {code: {"model": Error} for code in codes}


def error_response(request, status, code, message):
    return JSONResponse(
        status_code=status,
        content=dict(code=code, message=message, correlation_id=request.state.correlation_id),
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


@app.middleware("http")
async def request_context(request, call_next):
    correlation_id = str(uuid4())
    request.state.correlation_id = correlation_id
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    log.info(json.dumps({"correlation_id": correlation_id, "method": request.method,
                         "path": request.url.path, "status": response.status_code}))
    return response


@app.exception_handler(ServiceError)
async def service_error(request: Request, exc: ServiceError):
    return error_response(request, exc.status, exc.code, str(exc))


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    fields = ", ".join(sorted({str(item["loc"][-1]) for item in exc.errors()}))
    return error_response(request, 400, "invalid_request", f"Check these request fields: {fields}.")


@app.exception_handler(sqlite3.Error)
async def database_error(request: Request, exc: sqlite3.Error):
    log.error("Database error for request %s (%s)", request.state.correlation_id, type(exc).__name__)
    return error_response(request, 503, "database_unavailable", "The database request could not be completed.")


@app.get("/", include_in_schema=False)
def homepage():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready", responses=errors(503))
def ready():
    with database() as db:
        db.execute("SELECT id FROM spots LIMIT 1")
    return {"status": "ready"}


@app.post("/auth/token", response_model=Token, responses=errors(400, 401))
def login(request: Login):
    return Token(access_token=issue_token(request.username, request.password))


@app.get("/spots", response_model=list[Spot], responses=errors(400, 401, 503))
def spots(driver: Driver, starts_at: AwareDatetime, ends_at: AwareDatetime):
    return list_spots(starts_at, ends_at)


@app.post("/reservations", status_code=201, response_model=Reservation, responses=errors(400, 401, 404, 409, 503))
def reserve(request: ReservationInput, driver: Driver, request_key: RequestKey):
    return create_reservation(driver, request_key, request)


@app.get("/me/reservations", response_model=list[Reservation], responses=errors(401, 503))
def my_reservations(driver: Driver):
    return list_reservations(driver)


@app.get("/reservations/{reservation_id}", response_model=Reservation, responses=errors(400, 401, 404, 503))
def reservation(reservation_id: UUID, driver: Driver):
    return get_reservation(driver, reservation_id)


# FastAPI defaults to 422 for request validation. Our handler uses the documented 400.
default_openapi = app.openapi


def published_openapi():
    schema = default_openapi()
    for path in schema["paths"].values():
        for operation in path.values():
            operation["responses"].pop("422", None)
    for name in ("HTTPValidationError", "ValidationError"):
        schema.get("components", {}).get("schemas", {}).pop(name, None)
    return schema


app.openapi = published_openapi


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
