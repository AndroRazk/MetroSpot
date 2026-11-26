
"""Business logic for MetroSpot."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
from uuid import uuid4

import repository as repo

from config import (
    HOURLY_RATE_CENTS,
    HOLD_TTL_SECONDS
)


# Custom exception for business rule violations
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


# Calculate the reservation price in cents
def calculate_price(start, end):

    validate_window(start, end)

    duration = (end - start).total_seconds()

    hours = ceil(duration / 3600)

    return hours * HOURLY_RATE_CENTS


# Retrieve available parking spots
def list_spots(start, end):

    validate_window(start, end)

    with repo.database() as db:

        return repo.available_spots(
            db, start, end
        )


# Create a reservation awaiting payment
def create_reservation(
    driver_id,
    request_key,
    request,
    correlation_id=None
):

    # Normalize reservation timestamps
    request = request.model_copy(update={
        "starts_at": request.starts_at.astimezone(
            timezone.utc
        ),
        "ends_at": request.ends_at.astimezone(
            timezone.utc
        )
    })

    # Preserve fingerprints for existing approved requests
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

    # All database changes occur in one transaction
    with repo.database(write=True) as db:

        # Release expired, unprocessed reservations
        repo.expire_pending_reservations(db)

        # Check whether this request already exists
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

        # Validate reservation dates
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

        # Check whether the spot is already reserved
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

        # Calculate the parking price
        amount_cents = calculate_price(
            request.starts_at,
            request.ends_at
        )

        # Generate reservation ID and expiry time
        reservation_id = str(uuid4())

        expires_at = min(
            now + timedelta(seconds=HOLD_TTL_SECONDS),
            request.starts_at
        )

        # Save the reservation and payment task together
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


# Retrieve a reservation owned by the current driver
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


# Retrieve all reservations owned by the current driver
def list_reservations(driver_id):

    with repo.database(write=True) as db:

        repo.expire_pending_reservations(db)

        return repo.driver_reservations(
            db,
            driver_id
        )
