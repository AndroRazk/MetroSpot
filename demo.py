
"""Run the final MetroSpot demonstration against the live API."""

import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

BASE_URL = os.getenv("METROSPOT_URL", "http://127.0.0.1:8000")


def authenticate(username, password):
    response = requests.post(
        f"{BASE_URL}/auth/token",
        json={"username": username, "password": password},
        timeout=10
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def wait_for_status(reservation_id, headers, expected):
    for _ in range(20):
        response = requests.get(
            f"{BASE_URL}/reservations/{reservation_id}",
            headers=headers,
            timeout=10
        )
        assert response.status_code == 200

        booking = response.json()

        if booking["status"] == expected:
            return booking

        time.sleep(1)

    raise AssertionError(f"Expected reservation status: {expected}")


def main():

    # Authenticate both drivers
    drivers = [
        authenticate("driver-a", os.getenv("DRIVER_A_PASSWORD", "demo-a")),
        authenticate("driver-b", os.getenv("DRIVER_B_PASSWORD", "demo-b"))
    ]

    print("PASS: Both drivers authenticated")

    # Check service health
    for path in ("/health/live", "/health/ready"):
        response = requests.get(BASE_URL + path, timeout=10)
        assert response.status_code == 200
        assert response.headers.get("X-Correlation-ID")

    print("PASS: Health checks and correlation IDs working")

    # Find available parking spots
    start = datetime.now(timezone.utc) + timedelta(hours=2)

    window = {
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat()
    }

    response = requests.get(
        f"{BASE_URL}/spots",
        params=window,
        headers=drivers[0],
        timeout=10
    )

    assert response.status_code == 200
    spots = response.json()
    assert len(spots) >= 2

    spot = spots[0]["id"]

    # Two drivers attempt to reserve the same spot
    keys = [str(uuid4()), str(uuid4())]

    payloads = [
        {
            "spot_id": spot,
            "driver_name": f"Demo Driver {i}",
            "license_plate": f"TEST{i}",
            "payment_method": "approved",
            **window
        }
        for i in range(2)
    ]

    barrier = Barrier(2, timeout=5)

    def book(index):
        barrier.wait()

        return requests.post(
            f"{BASE_URL}/reservations",
            json=payloads[index],
            headers={
                **drivers[index],
                "Idempotency-Key": keys[index]
            },
            timeout=10
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(book, range(2)))

    assert sorted(r.status_code for r in outcomes) == [202, 409]

    winner = next(
        i for i, r in enumerate(outcomes)
        if r.status_code == 202
    )

    booking = outcomes[winner].json()
    reservation_id = booking["id"]

    print(f"PASS: Concurrent booking of {spot}: one HTTP 202 and one HTTP 409")

    # Wait for successful payment
    confirmed = wait_for_status(
        reservation_id, drivers[winner], "confirmed"
    )

    assert confirmed["payment_id"]

    print("PASS: Asynchronous payment confirmed")

    # Retry the same reservation
    response = requests.post(
        f"{BASE_URL}/reservations",
        json=payloads[winner],
        headers={
            **drivers[winner],
            "Idempotency-Key": keys[winner]
        },
        timeout=10
    )

    assert response.status_code == 202
    assert response.json()["id"] == reservation_id
    assert response.json()["payment_id"] == confirmed["payment_id"]

    print("PASS: Idempotent retry returned the original reservation and payment")

    # Verify reservation ownership
    path = f"{BASE_URL}/reservations/{reservation_id}"

    assert requests.get(
        path, headers=drivers[winner], timeout=10
    ).status_code == 200

    assert requests.get(
        path, headers=drivers[1 - winner], timeout=10
    ).status_code == 404

    assert requests.get(path, timeout=10).status_code == 401

    print("PASS: Authentication and ownership protection working")

    # Simulate a declined payment
    failed_spot = spots[1]["id"]

    response = requests.post(
        f"{BASE_URL}/reservations",
        json={
            "spot_id": failed_spot,
            "driver_name": "Demo Driver",
            "license_plate": "FAIL123",
            "payment_method": "declined",
            **window
        },
        headers={
            **drivers[0],
            "Idempotency-Key": str(uuid4())
        },
        timeout=10
    )

    assert response.status_code == 202

    wait_for_status(
        response.json()["id"], drivers[0], "failed"
    )

    print("PASS: Declined payment handled correctly")

    # Verify compensation
    response = requests.get(
        f"{BASE_URL}/spots",
        params=window,
        headers=drivers[0],
        timeout=10
    )

    assert response.status_code == 200

    available = {item["id"] for item in response.json()}

    assert failed_spot in available
    assert spot not in available

    print("PASS: Failed reservation released and confirmed reservation protected")

    print("\nAll 8 final demonstration tests passed.")
    print(f"Confirmed reservation ID: {reservation_id}")


if __name__ == "__main__":
    main()
