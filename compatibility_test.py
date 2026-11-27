
"""Test backward compatibility and reservation status filtering."""

import os
import requests


BASE_URL = os.getenv(
    "METROSPOT_URL",
    "http://127.0.0.1:8000"
)


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

    assert response.status_code == 200

    return {
        "Authorization": (
            f"Bearer {response.json()['access_token']}"
        )
    }


def main():

    driver_a = authenticate(
        "driver-a",
        os.getenv("DRIVER_A_PASSWORD", "demo-a")
    )

    driver_b = authenticate(
        "driver-b",
        os.getenv("DRIVER_B_PASSWORD", "demo-b")
    )

    print("PASS: Both drivers authenticated")


    # Old client without a status filter
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        timeout=5
    )

    assert response.status_code == 200

    all_reservations = response.json()

    assert isinstance(all_reservations, list)
    assert len(all_reservations) > 0

    print("PASS: Old client works without status filter")


    # Filter confirmed reservations
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        params={"status": "confirmed"},
        timeout=5
    )

    assert response.status_code == 200

    confirmed = response.json()

    expected_confirmed = [
        reservation
        for reservation in all_reservations
        if reservation["status"] == "confirmed"
    ]

    assert len(expected_confirmed) > 0
    assert confirmed == expected_confirmed

    print("PASS: Confirmed reservation filter works")


    # Filter failed reservations
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        params={"status": "failed"},
        timeout=5
    )

    assert response.status_code == 200

    failed = response.json()

    expected_failed = [
        reservation
        for reservation in all_reservations
        if reservation["status"] == "failed"
    ]

    assert len(expected_failed) > 0
    assert failed == expected_failed

    print("PASS: Failed reservation filter works")


    # Verify all supported reservation statuses
    statuses = [
        "pending_payment",
        "processing_payment",
        "confirmed",
        "failed",
        "expired",
        "payment_review"
    ]

    for status in statuses:

        response = requests.get(
            f"{BASE_URL}/me/reservations",
            headers=driver_a,
            params={"status": status},
            timeout=5
        )

        assert response.status_code == 200

        assert all(
            reservation["status"] == status
            for reservation in response.json()
        )

    print("PASS: All six reservation statuses supported")


    # Invalid status must be rejected
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        params={"status": "invalid"},
        timeout=5
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"

    print("PASS: Invalid status filter rejected")


    # Verify authentication remains required
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        params={"status": "confirmed"},
        timeout=5
    )

    assert response.status_code == 401

    print("PASS: Authentication remains required")


    # Verify ownership remains enforced
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_b,
        params={"status": "confirmed"},
        timeout=5
    )

    assert response.status_code == 200

    driver_a_ids = {
        reservation["id"]
        for reservation in all_reservations
    }

    driver_b_ids = {
        reservation["id"]
        for reservation in response.json()
    }

    assert driver_a_ids.isdisjoint(driver_b_ids)

    print("PASS: Status filtering preserves ownership")


    # Verify original behavior remains unchanged
    response = requests.get(
        f"{BASE_URL}/me/reservations",
        headers=driver_a,
        timeout=5
    )

    assert response.status_code == 200
    assert response.json() == all_reservations

    print("PASS: Original reservation history behavior unchanged")

    print("\nAll 9 backward compatibility tests passed")


if __name__ == "__main__":

    main()
