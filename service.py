
"""Business rules and reservation management for MetroSpot."""

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import repository as repo


# Custom exception for business rule violations
class ServiceError(Exception):

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


# Validate the requested reservation period
def validate_window(start, end):

    if end <= start:
        raise ServiceError(
            400,
            "invalid_window",
            "Departure must be after arrival."
        )


# Return available parking spots
def list_spots(start, end):

    validate_window(start, end)

    with repo.database() as db:
        return repo.available_spots(db, start, end)


# Create a new parking reservation
def create_reservation(driver_id, request_key, request):

    # Normalize timestamps to UTC
    request = request.model_copy(update={
        "starts_at": request.starts_at.astimezone(timezone.utc),
        "ends_at": request.ends_at.astimezone(timezone.utc),
    })

    # Generate a fingerprint of the request
    fingerprint = sha256(
        request.model_dump_json().encode()
    ).hexdigest()

    # Start a transaction with a write lock
    with repo.database(write=True) as db:

        # Check if this request was already submitted
        saved = repo.find_request(
            db, driver_id, request_key
        )

        if saved:

            # Reject reuse of a key with different request data
            if saved["fingerprint"] != fingerprint:
                raise ServiceError(
                    409,
                    "idempotency_conflict",
                    "This key belongs to a different booking request."
                )

            # Return the existing reservation
            return repo.owned_reservation(
                db,
                saved["reservation_id"],
                driver_id
            )

        # Validate the reservation period
        validate_window(
            request.starts_at,
            request.ends_at
        )

        # Prevent reservations in the past
        if request.starts_at < datetime.now(timezone.utc):
            raise ServiceError(
                400,
                "past_arrival",
                "Choose an arrival time in the future."
            )

        # Check if the parking spot exists
        spot = repo.find_spot(db, request.spot_id)

        if spot is None:
            raise ServiceError(
                404,
                "spot_not_found",
                "This spot does not exist."
            )

        # Check for overlapping reservations
        overlap = repo.find_overlap(
            db,
            request.spot_id,
            request.starts_at,
            request.ends_at
        )

        # Prevent double booking
        if spot["occupied"] or overlap:
            raise ServiceError(
                409,
                "spot_unavailable",
                "This spot is unavailable for the requested time."
            )

        # Generate a unique reservation ID
        reservation_id = str(uuid4())

        # Save the reservation and its idempotency key
        return repo.save_reservation(
            db,
            request,
            driver_id,
            request_key,
            fingerprint,
            reservation_id
        )


# Retrieve a reservation belonging to the current driver
def get_reservation(driver_id, reservation_id):

    with repo.database() as db:

        reservation = repo.owned_reservation(
            db,
            str(reservation_id),
            driver_id
        )

    if reservation is None:
        raise ServiceError(
            404,
            "reservation_not_found",
            "Reservation not found."
        )

    return reservation


# Retrieve all reservations belonging to the current driver
def list_reservations(driver_id):

    with repo.database() as db:
        return repo.driver_reservations(db, driver_id)
