
"""Database access and migrations for MetroSpot."""

import sqlite3
import json
import time

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import uuid4

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
                connection.execute("BEGIN IMMEDIATE")

            yield connection


# Apply database migrations
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

        # Migration 2: Reservation idempotency
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

        # Migration 3: Payment states and expiry
        if version < 3:

            db.execute("""
                ALTER TABLE reservations
                ADD COLUMN status TEXT NOT NULL
                DEFAULT 'confirmed'
                CHECK (status IN (
                    'pending_payment',
                    'processing_payment',
                    'confirmed',
                    'failed',
                    'expired',
                    'payment_review'
                ))
            """)

            db.execute("""
                ALTER TABLE reservations
                ADD COLUMN expires_at TEXT
            """)

            db.execute("""
                ALTER TABLE reservations
                ADD COLUMN payment_id TEXT
            """)

            db.execute("""
                ALTER TABLE reservations
                ADD COLUMN amount_cents INTEGER
                NOT NULL DEFAULT 0
            """)

            db.execute("""
                ALTER TABLE reservations
                ADD COLUMN payment_method TEXT
                NOT NULL DEFAULT 'approved'
            """)

            db.execute("PRAGMA user_version = 3")

        # Migration 4: Durable payment outbox
        if version < 4:

            db.execute("""
                CREATE TABLE payment_outbox (
                    id TEXT PRIMARY KEY,

                    reservation_id TEXT NOT NULL
                        REFERENCES reservations(id),

                    idempotency_key TEXT UNIQUE NOT NULL,

                    correlation_id TEXT NOT NULL,

                    payload TEXT NOT NULL,

                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending',
                            'processing',
                            'completed',
                            'failed',
                            'review'
                        )),

                    attempts INTEGER NOT NULL DEFAULT 0,

                    next_attempt_at REAL NOT NULL,

                    created_at REAL NOT NULL,

                    lease_until REAL,

                    last_error TEXT,

                    UNIQUE (reservation_id)
                )
            """)

            db.execute("""
                CREATE INDEX payment_outbox_pending
                ON payment_outbox (
                    status, next_attempt_at
                )
            """)

            db.execute("PRAGMA user_version = 4")


# Convert timestamps to UTC
def timestamp(value):

    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    )


# Expire reservations that have not started payment
def expire_pending_reservations(db):

    now = timestamp(datetime.now(timezone.utc))

    db.execute("""
        UPDATE reservations
        SET status = 'expired'
        WHERE status = 'pending_payment'
        AND expires_at <= ?
        AND EXISTS (
            SELECT 1 FROM payment_outbox
            WHERE reservation_id = reservations.id
            AND status = 'pending'
            AND attempts = 0
        )
    """, (now,))

    db.execute("""
        UPDATE payment_outbox
        SET status = 'failed',
            last_error = 'Reservation expired'
        WHERE status = 'pending'
        AND attempts = 0
        AND reservation_id IN (
            SELECT id FROM reservations
            WHERE status = 'expired'
        )
    """)


# Find available parking spots
def available_spots(db, start, end):

    now = timestamp(datetime.now(timezone.utc))

    rows = db.execute("""
        SELECT id FROM spots
        WHERE NOT occupied
        AND NOT EXISTS (
            SELECT 1 FROM reservations
            WHERE spot_id = spots.id
            AND starts_at < ?
            AND ends_at > ?
            AND (
                status IN (
                    'confirmed',
                    'processing_payment',
                    'payment_review'
                )
                OR (
                    status = 'pending_payment'
                    AND expires_at > ?
                )
            )
        )
        ORDER BY id
    """, (
        timestamp(end),
        timestamp(start),
        now
    )).fetchall()

    return [dict(row) for row in rows]


# Find a parking spot
def find_spot(db, spot_id):

    return db.execute(
        "SELECT occupied FROM spots WHERE id = ?",
        (spot_id,)
    ).fetchone()


# Check whether a reservation overlaps another
def find_overlap(db, spot_id, start, end):

    now = timestamp(datetime.now(timezone.utc))

    return db.execute("""
        SELECT 1 FROM reservations
        WHERE spot_id = ?
        AND starts_at < ?
        AND ends_at > ?
        AND (
            status IN (
                'confirmed',
                'processing_payment',
                'payment_review'
            )
            OR (
                status = 'pending_payment'
                AND expires_at > ?
            )
        )
        LIMIT 1
    """, (
        spot_id,
        timestamp(end),
        timestamp(start),
        now
    )).fetchone()


# Find a previously submitted request
def find_request(db, driver, key):

    return db.execute("""
        SELECT fingerprint, reservation_id
        FROM reservation_requests
        WHERE driver_id = ? AND request_key = ?
    """, (
        driver,
        key
    )).fetchone()


# Save a temporary reservation and its payment task
def save_reservation(
    db,
    request,
    driver,
    key,
    fingerprint,
    reservation_id,
    amount_cents,
    expires_at,
    correlation_id
):

    # Create the temporary reservation
    db.execute("""
        INSERT INTO reservations (
            id,
            driver_id,
            spot_id,
            starts_at,
            ends_at,
            driver_name,
            license_plate,
            status,
            expires_at,
            amount_cents,
            payment_method
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        reservation_id,
        driver,
        request.spot_id,
        timestamp(request.starts_at),
        timestamp(request.ends_at),
        request.driver_name,
        request.license_plate,
        "pending_payment",
        timestamp(expires_at),
        amount_cents,
        request.payment_method
    ))

    # Save the reservation idempotency key
    db.execute("""
        INSERT INTO reservation_requests (
            driver_id,
            request_key,
            fingerprint,
            reservation_id
        )
        VALUES (?, ?, ?, ?)
    """, (
        driver,
        key,
        fingerprint,
        reservation_id
    ))

    # Prepare the payment provider request
    payload = {
        "reservation_id": reservation_id,
        "amount_cents": amount_cents,
        "payment_method": request.payment_method
    }

    now = time.time()

    # Save the payment operation in the outbox
    db.execute("""
        INSERT INTO payment_outbox (
            id,
            reservation_id,
            idempotency_key,
            correlation_id,
            payload,
            status,
            attempts,
            next_attempt_at,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(uuid4()),
        reservation_id,
        f"payment-{reservation_id}",
        correlation_id,
        json.dumps(payload),
        "pending",
        0,
        now,
        now
    ))

    return owned_reservation(
        db, reservation_id, driver
    )


# Retrieve a reservation belonging to a driver
def owned_reservation(db, reservation_id, driver):

    row = db.execute("""
        SELECT
            id,
            spot_id,
            starts_at,
            ends_at,
            status,
            amount_cents,
            expires_at,
            payment_id
        FROM reservations
        WHERE id = ? AND driver_id = ?
    """, (
        str(reservation_id),
        driver
    )).fetchone()

    return dict(row) if row is not None else None


# Retrieve all reservations belonging to a driver
def driver_reservations(db, driver):

    rows = db.execute("""
        SELECT
            id,
            spot_id,
            starts_at,
            ends_at,
            status,
            amount_cents,
            expires_at,
            payment_id
        FROM reservations
        WHERE driver_id = ?
        ORDER BY starts_at DESC
    """, (driver,)).fetchall()

    return [dict(row) for row in rows]


# Check whether the database is ready
def check_readiness():

    with database() as db:

        db.execute(
            "SELECT id FROM spots LIMIT 1"
        ).fetchone()
