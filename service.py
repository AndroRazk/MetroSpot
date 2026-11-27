
"""MetroSpot business logic and reservation rules."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
from uuid import uuid4

import repository as repo

from config import HOURLY_RATE_CENTS, HOLD_TTL_SECONDS


# Business rule exception
class ServiceError(Exception):

    def __init__(self, status, code, message):

        super().__init__(message)

        self.status = status
        self.code = code


# Validate the reservation time window
def validate_window(start, end):

    if end <= start:

        raise ServiceError(
            400,
            "invalid_window",
            "Departure must be after arrival."
        )


# Calculate the reservation price
def calculate_price(start, end):

    validate_window(start, end)

    duration = (end - start).total_seconds()

    return ceil(duration / 3600) * HOURLY_RATE_CENTS


# Retrieve available parking spots
def list_spots(start, end):

    validate_window(start, end)

    with repo.database() as db:

        return repo.available_spots(db, start, end)


# Create a reservation and its payment operation
def create_reservation(
    driver_id,
    request_key,
    request,
    correlation_id=None
):

    request = request.model_copy(update={
        "starts_at": request.starts_at.astimezone(timezone.utc),
        "ends_at": request.ends_at.astimezone(timezone.utc)
    })

    excluded = (
        {"payment_method"}
        if request.payment_method == "approved"
        else set()
    )

    fingerprint = sha256(
        request.model_dump_json(
            exclude=excluded
        ).encode()
    ).hexdigest()

    if correlation_id is None:

        correlation_id = str(uuid4())

    with repo.database(write=True) as db:

        repo.expire_pending_reservations(db)

        # Check whether the request was already processed
        previous = repo.find_request(
            db,
            driver_id,
            request_key
        )

        if previous is not None:

            if previous["fingerprint"] != fingerprint:

                raise ServiceError(
                    409,
                    "idempotency_conflict",
                    "This idempotency key was already used "
                    "with different reservation details."
                )

            return repo.owned_reservation(
                db,
                previous["reservation_id"],
                driver_id
            )

        # Validate the requested time window
        validate_window(
            request.starts_at,
            request.ends_at
        )

        now = datetime.now(timezone.utc)

        if request.starts_at < now:

            raise ServiceError(
                400,
                "past_arrival",
                "Arrival cannot be in the past."
            )

        # Verify that the parking spot exists
        spot = repo.find_spot(
            db,
            request.spot_id
        )

        if spot is None:

            raise ServiceError(
                404,
                "spot_not_found",
                "The requested parking spot does not exist."
            )

        # Prevent overlapping reservations
        overlap = repo.find_overlap(
            db,
            request.spot_id,
            request.starts_at,
            request.ends_at
        )

        if spot["occupied"] or overlap is not None:

            raise ServiceError(
                409,
                "spot_unavailable",
                "The parking spot is not available "
                "for the requested time."
            )

        # Calculate the reservation price
        amount_cents = calculate_price(
            request.starts_at,
            request.ends_at
        )

        reservation_id = str(uuid4())

        expires_at = min(
            now + timedelta(seconds=HOLD_TTL_SECONDS),
            request.starts_at
        )

        # Save reservation and payment operation atomically
        return repo.save_reservation(
            db=db,
            request=request,
            driver=driver_id,
            key=request_key,
            fingerprint=fingerprint,
            reservation_id=reservation_id,
            amount_cents=amount_cents,
            expires_at=expires_at,
            correlation_id=correlation_id
        )


# Retrieve a reservation belonging to the driver
def get_reservation(driver_id, reservation_id):

    with repo.database(write=True) as db:

        repo.expire_pending_reservations(db)

        reservation = repo.owned_reservation(
            db,
            reservation_id,
            driver_id
        )

        if reservation is None:

            raise ServiceError(
                404,
                "reservation_not_found",
                "Reservation not found."
            )

        return reservation


# Retrieve the driver's reservations
# The optional status filter preserves existing behavior
def list_reservations(driver_id, status=None):

    with repo.database(write=True) as db:

        repo.expire_pending_reservations(db)

        reservations = repo.driver_reservations(
            db,
            driver_id
        )

        # Existing clients receive all reservations
        if status is None:

            return reservations

        # New clients can filter reservations by status
        return [
            reservation
            for reservation in reservations
            if reservation["status"] == status
        ]
