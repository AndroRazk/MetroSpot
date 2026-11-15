"""Run the milestone checks against a live MetroSpot API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from threading import Barrier
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, *, body=None, token=None, key=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    if body is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(body).encode() if body is not None else None
    try:
        response = urlopen(Request(base + path, data=data, headers=headers), timeout=15)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def check(base):
    tokens = []
    for suffix in ("a", "b"):
        variable = f"DRIVER_{suffix.upper()}_PASSWORD"
        password = os.getenv(variable, f"demo-{suffix}")
        status, result = request(base, "/auth/token", body=dict(username=f"driver-{suffix}", password=password))
        assert status == 200, (status, result)
        tokens.append(result["access_token"])

    status, contract = request(base, "/openapi.json")
    assert status == 200 and "409" in contract["paths"]["/reservations"]["post"]["responses"]
    print("PASS: OpenAPI publishes the reservation conflict response")

    start = datetime.now(timezone.utc) + timedelta(hours=2)
    window = dict(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=1)).isoformat())
    status, spots = request(base, "/spots?" + urlencode(window), token=tokens[0])
    assert status == 200 and spots, (status, spots)
    spot = next((item["id"] for item in spots if item["id"] == "P-024"), spots[0]["id"])
    keys = [str(uuid4()), str(uuid4())]
    payloads = [dict(spot_id=spot, driver_name=f"Demo Driver {i}", license_plate=f"TEST{i}", **window)
                for i in range(2)]
    barrier = Barrier(2, timeout=5)

    def book(index):
        barrier.wait()
        return request(base, "/reservations", body=payloads[index], token=tokens[index], key=keys[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(book, range(2)))
    assert sorted(status for status, _ in outcomes) == [201, 409], outcomes
    winner = next(index for index, result in enumerate(outcomes) if result[0] == 201)
    assert outcomes[1 - winner][1]["code"] == "spot_unavailable", outcomes
    booking = outcomes[winner][1]
    print(f"PASS: {spot} received one HTTP 201 and one HTTP 409")

    assert request(base, "/reservations", body=payloads[winner], token=tokens[winner],
                   key=keys[winner]) == (201, booking)
    status, conflict = request(base, "/reservations",
                               body=payloads[winner] | {"license_plate": "NEWPLATE"},
                               token=tokens[winner], key=keys[winner])
    assert status == 409 and conflict["code"] == "idempotency_conflict", conflict
    status, items = request(base, "/spots?" + urlencode(window), token=tokens[winner])
    assert status == 200 and spot not in {item["id"] for item in items}
    matching = []
    for token in tokens:
        status, reservations = request(base, "/me/reservations", token=token)
        assert status == 200, (status, reservations)
        matching.extend(row for row in reservations if row["spot_id"] == spot and
                        datetime.fromisoformat(row["starts_at"]) == start)
    assert matching == [booking], matching
    print("PASS: Idempotent retry returned the original ID. One booking was saved.")

    path = f"/reservations/{booking['id']}"
    assert request(base, path, token=tokens[winner]) == (200, booking)
    assert request(base, path)[0] == 401
    assert request(base, path, token=tokens[1 - winner])[0] == 404
    invalid_window = dict(window, ends_at=window["starts_at"])
    assert request(base, "/spots?" + urlencode(invalid_window), token=tokens[winner])[0] == 400
    print("PASS: Own booking accessible, missing login rejected, other booking hidden, bad dates rejected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    check(args.base_url.rstrip("/"))
