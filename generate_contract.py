
"""Generate and document the MetroSpot OpenAPI contract."""

import json
import yaml

from app import default_openapi


# Generate the contract directly from the current API
contract = default_openapi()


# Match the actual validation error responses
for path in contract["paths"].values():

    for operation in path.values():

        operation["responses"].pop("422", None)


# Remove unused validation error schemas
for name in (
    "HTTPValidationError",
    "ValidationError"
):

    contract.get("components", {}).get(
        "schemas", {}
    ).pop(name, None)


# General API description
contract["info"]["description"] = """
MetroSpot is a parking reservation service that allows
authenticated drivers to search for available parking spots,
submit reservations and track their payment status.

Reservations are processed asynchronously through an
independent payment provider using a durable outbox.

All reservation operations enforce ownership and prevent
overlapping bookings for the same parking spot.
"""


paths = contract["paths"]


# Authentication
paths["/auth/token"]["post"]["description"] = """
Authenticates a driver using their username and password.

Returns a JWT access token that must be included in the
Authorization header as a Bearer token for protected endpoints.
"""


# Available parking spots
paths["/spots"]["get"]["description"] = """
Returns parking spots available during the requested time window.

The departure time must be later than the arrival time.

Confirmed reservations, active pending reservations and
unresolved payment operations prevent overlapping bookings.
"""


# Reservation creation
reservation = paths["/reservations"]["post"]

reservation["description"] = """
Submits a parking reservation for asynchronous payment processing.

A successful request returns HTTP 202 Accepted. This does not
guarantee that the payment has been approved.

The reservation and its payment outbox operation are saved
atomically in the database.

An Idempotency-Key header is required. Reusing the same key
with identical reservation details returns the original
reservation. Reusing the key with different details returns
HTTP 409 Conflict.

Idempotency keys are scoped to the authenticated driver.

Overlapping reservations for the same parking spot are rejected.
"""


# Document reservation response codes
response_descriptions = {
    "202": "Reservation accepted or existing idempotent reservation returned.",
    "400": "Invalid request data or reservation time window.",
    "401": "Missing or invalid authentication token.",
    "404": "Requested parking spot does not exist.",
    "409": "Parking spot unavailable or idempotency key conflict.",
    "503": "Database temporarily unavailable."
}

for code, description in response_descriptions.items():

    reservation["responses"][code]["description"] = description


# Document idempotency header
for parameter in reservation.get("parameters", []):

    if parameter["name"].lower() == "idempotency-key":

        parameter["description"] = (
            "Unique request key scoped to the authenticated driver. "
            "Reuse the same key when retrying the same reservation."
        )


# Retrieve individual reservation
paths["/reservations/{reservation_id}"]["get"]["description"] = """
Returns the current status of a reservation.

Only the reservation owner can retrieve it.

A nonexistent reservation or a reservation belonging to
another driver returns HTTP 404 to avoid exposing private data.

Clients can poll this endpoint to track payment progress.
"""


# Reservation history
paths["/me/reservations"]["get"]["description"] = """
Returns reservations belonging to the authenticated driver,
including pending, processing, confirmed, failed, expired
and payment review reservations.

Version 0.3.0 introduces an optional status query parameter
that allows drivers to filter their reservations.

If the status parameter is omitted, all reservations belonging
to the authenticated driver are returned, preserving
compatibility with version 0.2.0 clients.

An invalid status value returns HTTP 400.
"""

# Health endpoints
paths["/health/live"]["get"]["description"] = """
Indicates whether the MetroSpot API process is running.
"""


paths["/health/ready"]["get"]["description"] = """
Checks whether the MetroSpot API can access its database.

Payment provider availability is not required because payment
operations are processed asynchronously.
"""


# Reservation model descriptions
schemas = contract["components"]["schemas"]

properties = schemas["Reservation"]["properties"]

properties["status"]["description"] = """
Reservation lifecycle:

pending_payment: Waiting for payment processing.

processing_payment: Payment processing has started.

confirmed: Payment approved and reservation confirmed.

failed: Payment definitively declined and parking spot released.

expired: Unprocessed reservation exceeded its holding period.

payment_review: Payment outcome remains uncertain after
automatic retries. Operator reconciliation is required.
"""


properties["amount_cents"]["description"] = (
    "Reservation price in cents."
)

properties["expires_at"]["description"] = (
    "Expiration time of the initial unpaid reservation hold."
)

properties["payment_id"]["description"] = (
    "Payment identifier returned by the payment provider, "
    "when available."
)


# Payment simulation input
schemas["ReservationInput"]["properties"][
    "payment_method"
]["description"] = (
    "Simulated payment outcome for demonstration purposes. "
    "Use approved for a successful payment or declined "
    "for a rejected payment."
)


# Export the JSON contract
with open("openapi.json", "w") as file:

    json.dump(contract, file, indent=2)


# Export the YAML contract
with open("openapi.yaml", "w") as file:

    yaml.safe_dump(
        contract,
        file,
        sort_keys=False,
        allow_unicode=True
    )


print("PASS: Extended OpenAPI contract generated")
