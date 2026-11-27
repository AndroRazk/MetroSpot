
"""Verify that MetroSpot conforms to its published API contract."""

import os
import time
import yaml
import requests

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker


BASE_URL = os.getenv(
    "METROSPOT_URL",
    "http://127.0.0.1:8000"
)

DRIVER_A_PASSWORD = os.getenv(
    "DRIVER_A_PASSWORD",
    "demo-a"
)

DRIVER_B_PASSWORD = os.getenv(
    "DRIVER_B_PASSWORD",
    "demo-b"
)


# Load the published OpenAPI contract
with open(
    os.getenv("METROSPOT_CONTRACT", "openapi.yaml")
) as file:

    contract = yaml.safe_load(file)


# Validate an HTTP response against the contract
def validate_response(method, path, response, expected_status):

    assert response.status_code == expected_status, (
        f"{method} {path}: expected {expected_status}, "
        f"received {response.status_code}: {response.text}"
    )

    operation = contract["paths"][path][method.lower()]

    responses = operation["responses"]

    assert str(response.status_code) in responses, (
        "Response status is not documented"
    )

    specification = responses[str(response.status_code)]

    content = specification.get("content", {})

    assert "application/json" in content

    assert "application/json" in response.headers.get(
        "Content-Type", ""
    )

    schema = content["application/json"]["schema"]

    # Include components so local schema references can be resolved
    validation_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "components": contract["components"],
        **schema
    }

    validator = Draft202012Validator(
        validation_schema,
        format_checker=FormatChecker()
    )

    validator.validate(response.json())

    assert response.headers.get("X-Correlation-ID")

    if expected_status >= 400:

        assert response.json()["correlation_id"] == (
            response.headers["X-Correlation-ID"]
        )

    return response.json()


# Authenticate a driver
def authenticate(username, password):

    response = requests.post(
        f"{BASE_URL}/auth/token",
        json={
            "username": username,
            "password": password
        },
        timeout=5
    )

    result = validate_response(
        "POST",
        "/auth/token",
        response,
        200
    )

    return {
        "Authorization": f"Bearer {result['access_token']}"
    }


# Wait for a reservation to reach its expected status
def wait_for_status(reservation_id, headers, expected):

    deadline = time.monotonic() + 15

    while time.monotonic() < deadline:

        response = requests.get(
            f"{BASE_URL}/reservations/{reservation_id}",
            headers=headers,
            timeout=5
        )

        result = validate_response(
            "GET",
            "/reservations/{reservation_id}",
            response,
            200
        )

        if result["status"] == expected:

            return result

        time.sleep(0.5)

    raise AssertionError(
        f"Reservation did not reach {expected}"
    )


def main():

    # Test 1: Liveness
    response = requests.get(
        f"{BASE_URL}/health/live",
        timeout=5
    )

    validate_response(
        "GET", "/health/live", response, 200
    )

    assert response.json()["status"] == "ok"

    print("PASS: Liveness response conforms")


    # Test 2: Readiness
    response = requests.get(
        f"{BASE_URL}/health/ready",
        timeout=5
    )

    validate_response(
        "GET", "/health/ready", response, 200
    )

    assert response.json()["status"] == "ready"

    print("PASS: Readiness response conforms")


    # Test 3: Authentication
    driver_a = authenticate(
        "driver-a",
        DRIVER_A_PASSWORD
    )

    driver_b = authenticate(
        "driver-b",
        DRIVER_B_PASSWORD
    )

    print("PASS: Authentication responses conform")


    # Test 4: Invalid credentials
    response = requests.post(
        f"{BASE_URL}/auth/token",
        json={
            "username": "driver-a",
            "password": str(uuid4())
        },
        timeout=5
    )

    validate_response(
        "POST", "/auth/token", response, 401
    )

    print("PASS: Invalid credentials response conforms")


    # Prepare a future reservation window
    start = datetime.now(timezone.utc) + timedelta(days=1)
    end = start + timedelta(hours=2)

    params = {
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat()
    }


    # Test 5: Missing authentication
    response = requests.get(
        f"{BASE_URL}/spots",
        params=params,
        timeout=5
    )

    validate_response(
        "GET", "/spots", response, 401
    )

    print("PASS: Missing authentication response conforms")


    # Test 6: Available parking spots
    response = requests.get(
        f"{BASE_URL}/spots",
        params=params,
        headers=driver_a,
        timeout=5
    )

    spots = validate_response(
        "GET", "/spots", response, 200
    )

    assert len(spots) >= 2

    spot_id = spots[0]["id"]

    print("PASS: Available parking spots response conforms")


    payload = {
        "spot_id": spot_id,
        "starts_at": start.isoformat(),
        "ends_at": end.isoformat(),
        "driver_name": "Driver A",
        "license_plate": "ABC123",
        "payment_method": "approved"
    }

    booking_headers = {
        **driver_a,
        "Idempotency-Key": str(uuid4())
    }


    # Test 7: Asynchronous reservation
    response = requests.post(
        f"{BASE_URL}/reservations",
        json=payload,
        headers=booking_headers,
        timeout=5
    )

    reservation = validate_response(
        "POST", "/reservations", response, 202
    )

    reservation_id = reservation["id"]

    assert reservation["status"] == "pending_payment"

    print("PASS: Asynchronous reservation response conforms")


    # Test 8: Idempotent retry
    response = requests.post(
        f"{BASE_URL}/reservations",
        json=payload,
        headers=booking_headers,
        timeout=5
    )

    result = validate_response(
        "POST", "/reservations", response, 202
    )

    assert result["id"] == reservation_id

    print("PASS: Idempotent retry response conforms")


    # Test 9: Idempotency conflict
    response = requests.post(
        f"{BASE_URL}/reservations",
        json={
            **payload,
            "license_plate": "XYZ789"
        },
        headers=booking_headers,
        timeout=5
    )

    result = validate_response(
        "POST", "/reservations", response, 409
    )

    assert result["code"] == "idempotency_conflict"

    print("PASS: Idempotency conflict response conforms")


    # Test 10: Overlapping reservation
    response = requests.post(
        f"{BASE_URL}/reservations",
        json=payload,
        headers={
            **driver_b,
            "Idempotency-Key": str(uuid4())
        },
        timeout=5
    )

    result = validate_response(
        "POST", "/reservations", response, 409
    )

    assert result["code"] == "spot_unavailable"

    print("PASS: Reservation conflict response conforms")


    # Test 11: Successful payment confirmation
    confirmed = wait_for_status(
        reservation_id,
        driver_a,
        "confirmed"
    )

    assert confirmed["payment_id"] is not None

    print("PASS: Confirmed reservation response conforms")


    # Test 12: Ownership protection
    response = requests.get(
        f"{BASE_URL}/reservations/{reservation_id}",
        headers=driver_b,
        timeout=5
    )

    validate_response(
        "GET",
        "/reservations/{reservation_id}",
        response,
        404
    )

    print("PASS: Ownership protection response conforms")


    # Test 13: Nonexistent reservation
    response = requests.get(
        f"{BASE_URL}/reservations/{uuid4()}",
        headers=driver_a,
        timeout=5
    )

    validate_response(
        "GET",
        "/reservations/{reservation_id}",
        response,
        404
    )

    print("PASS: Nonexistent reservation response conforms")


    # Test 14: Invalid reservation window
    response = requests.post(
        f"{BASE_URL}/reservations",
        json={
            **payload,
            "ends_at": start.isoformat()
        },
        headers={
            **driver_a,
            "Idempotency-Key": str(uuid4())
        },
        timeout=5
    )

    validate_response(
        "POST", "/reservations", response, 400
    )

    print("PASS: Invalid time window response conforms")


    # Test 15: Missing idempotency key
    response = requests.post(
        f"{BASE_URL}/reservations",
        json=payload,
        headers=driver_a,
        timeout=5
    )

    validate_response(
        "POST", "/reservations", response, 400
    )

    print("PASS: Missing idempotency key response conforms")


    # Test 16: Invalid payment method
    response = requests.post(
        f"{BASE_URL}/reservations",
        json={
            **payload,
            "payment_method": "invalid"
        },
        headers={
            **driver_a,
            "Idempotency-Key": str(uuid4())
        },
        timeout=5
    )

    validate_response(
        "POST", "/reservations", response, 400
    )

    print("PASS: Invalid payment method response conforms")


    # Test 17: Declined payment
    response = requests.post(
        f"{BASE_URL}/reservations",
        json={
            **payload,
            "spot_id": spots[1]["id"],
            "payment_method": "declined"
        },
        headers={
            **driver_a,
            "Idempotency-Key": str(uuid4())
        },
        timeout=5
    )

    declined = validate_response(
        "POST", "/reservations", response, 202
    )

    wait_for_status(
        declined["id"],
        driver_a,
        "failed"
    )

    print("PASS: Declined payment response conforms")


    # Test 18: Reservation history
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        timeout=5
    )

    history = validate_response(
        "GET", "/me/reservations", response, 200
    )

    assert reservation_id in [
        item["id"] for item in history
    ]

    print("PASS: Reservation history response conforms")


    # Test 19: Reservation history ownership
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_b,
        timeout=5
    )

    history = validate_response(
        "GET", "/me/reservations", response, 200
    )

    assert reservation_id not in [
        item["id"] for item in history
    ]

    print("PASS: Reservation history ownership enforced")


    print("\nAll 19 API conformance tests passed")


if __name__ == "__main__":

    main()
