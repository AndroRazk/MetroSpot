
"""Database access and migrations for MetroSpot."""

import sqlite3

from contextlib import closing, contextmanager
from datetime import timezone

from config import DB_PATH


# Open a database connection
@contextmanager
def database(*, write=False):
    with closing(
        sqlite3.connect(DB_PATH, timeout=10)
    ) as connection:

        connection.row_factory = sqlite3.Row

        connection.execute("PRAGMA foreign_keys = ON")

        with connection:
            if write:
                # Acquire the write lock before checking availability
                connection.execute("BEGIN IMMEDIATE")

            yield connection


# Apply database migrations in order
def migrate():
    with database(write=True) as db:

        version = db.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        # Migration 1: Parking spots and reservations
        if version < 1:

            db.execute("""
                CREATE TABLE spots (
                    id TEXT PRIMARY KEY,
                    occupied INTEGER NOT NULL DEFAULT 0
                )
            """)

            db.executemany(
                "INSERT INTO spots (id) VALUES (?)",
                [(f"P-{i:03}",) for i in range(1, 201)]
            )

            db.execute("""
                CREATE TABLE reservations (
                    id TEXT PRIMARY KEY,
                    driver_id TEXT NOT NULL,
                    spot_id TEXT NOT NULL REFERENCES spots(id),
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL
                        CHECK (ends_at > starts_at),
                    driver_name TEXT NOT NULL,
                    license_plate TEXT NOT NULL
                )
            """)

            db.execute("""
                CREATE INDEX reservations_spot_time
                ON reservations (
                    spot_id, starts_at, ends_at
                )
            """)

            db.execute("PRAGMA user_version = 1")

        # Migration 2: Idempotent reservation requests
        if version < 2:

            db.execute("""
                CREATE TABLE reservation_requests (
                    driver_id TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    reservation_id TEXT NOT NULL
                        REFERENCES reservations(id),
                    PRIMARY KEY (driver_id, request_key)
                )
            """)

            db.execute("PRAGMA user_version = 2")


# Convert timestamps to a consistent UTC format
def timestamp(value):
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )


# Find available parking spots
def available_spots(db, start, end):

    rows = db.execute("""
        SELECT id FROM spots
        WHERE NOT occupied
        AND NOT EXISTS (
            SELECT 1 FROM reservations
            WHERE spot_id = spots.id
            AND starts_at < ?
            AND ends_at > ?
        )
        ORDER BY id
    """, (timestamp(end), timestamp(start))).fetchall()

    return [dict(row) for row in rows]


# Find a parking spot
def find_spot(db, spot_id):

    return db.execute(
        "SELECT occupied FROM spots WHERE id = ?",
        (spot_id,)
    ).fetchone()


# Check whether a reservation overlaps another
def find_overlap(db, spot_id, start, end):

    return db.execute("""
        SELECT 1 FROM reservations
        WHERE spot_id = ?
        AND starts_at < ?
        AND ends_at > ?
        LIMIT 1
    """, (
        spot_id,
        timestamp(end),
        timestamp(start)
    )).fetchone()


# Find a previously submitted reservation request
def find_request(db, driver, key):

    return db.execute("""
        SELECT fingerprint, reservation_id
        FROM reservation_requests
        WHERE driver_id = ? AND request_key = ?
    """, (driver, key)).fetchone()


# Save a new reservation and its idempotency key
def save_reservation(
    db, request, driver, key,
    fingerprint, reservation_id
):

    db.execute("""
        INSERT INTO reservations (
            id, driver_id, spot_id,
            starts_at, ends_at,
            driver_name, license_plate
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        reservation_id,
        driver,
        request.spot_id,
        timestamp(request.starts_at),
        timestamp(request.ends_at),
        request.driver_name,
        request.license_plate
    ))

    db.execute("""
        INSERT INTO reservation_requests (
            driver_id, request_key,
            fingerprint, reservation_id
        )
        VALUES (?, ?, ?, ?)
    """, (
        driver,
        key,
        fingerprint,
        reservation_id
    ))

    return owned_reservation(
        db, reservation_id, driver
    )


# Retrieve a reservation belonging to a specific driver
def owned_reservation(db, reservation_id, driver):

    row = db.execute("""
        SELECT id, spot_id, starts_at, ends_at
        FROM reservations
        WHERE id = ? AND driver_id = ?
    """, (reservation_id, driver)).fetchone()

    return dict(row) if row is not None else None


# Retrieve all reservations belonging to a driver
def driver_reservations(db, driver):

    rows = db.execute("""
        SELECT id, spot_id, starts_at, ends_at
        FROM reservations
        WHERE driver_id = ?
        ORDER BY starts_at DESC
    """, (driver,)).fetchall()

    return [dict(row) for row in rows]

# Check whether the database is ready
def check_readiness():

    with database() as db:
        db.execute("SELECT id FROM spots LIMIT 1").fetchone()