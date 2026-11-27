
"""Background payment worker for MetroSpot."""

import json
import logging
import time

import requests

import repository as repo

from config import (
    PAYMENT_PROVIDER_URL,
    PAYMENT_TIMEOUT_SECONDS,
    PAYMENT_MAX_ATTEMPTS,
    PAYMENT_RETRY_BASE_SECONDS,
    PAYMENT_LEASE_SECONDS,
    WORKER_POLL_SECONDS
)

log = logging.getLogger("metrospot.worker")

# Write structured payment logs
def log_event(event, task, **details):

    log.info(json.dumps({
        "component": "payment_worker",
        "event": event,
        "correlation_id": task["correlation_id"],
        "reservation_id": task["reservation_id"],
        "attempt": task["attempts"],
        **details
    }))

# Claim the next available payment task
def claim_task():

    with repo.database(write=True) as db:

        repo.expire_pending_reservations(db)

        now = time.time()

        # Recover tasks abandoned by a crashed worker
        db.execute("""
            UPDATE payment_outbox
            SET status = 'pending',
                lease_until = NULL,
                next_attempt_at = ?
            WHERE status = 'processing'
            AND lease_until <= ?
        """, (now, now))

        # Find the next payment task
        task = db.execute("""
            SELECT * FROM payment_outbox
            WHERE status = 'pending'
            AND next_attempt_at <= ?
            ORDER BY created_at
            LIMIT 1
        """, (now,)).fetchone()

        if task is None:
            return None

        task = dict(task)

        # Stop retrying if the maximum was reached
        if task["attempts"] >= PAYMENT_MAX_ATTEMPTS:

            db.execute("""
                UPDATE payment_outbox
                SET status = 'review',
                    lease_until = NULL,
                    last_error = 'Maximum attempts reached'
                WHERE id = ?
            """, (task["id"],))

            db.execute("""
                UPDATE reservations
                SET status = 'payment_review'
                WHERE id = ?
            """, (task["reservation_id"],))

            return None

        # Claim the payment operation
        lease = now + PAYMENT_LEASE_SECONDS

        db.execute("""
            UPDATE payment_outbox
            SET status = 'processing',
                attempts = attempts + 1,
                lease_until = ?
            WHERE id = ?
        """, (
            lease,
            task["id"]
        ))

        db.execute("""
            UPDATE reservations
            SET status = 'processing_payment'
            WHERE id = ?
        """, (task["reservation_id"],))

        task["attempts"] += 1
        task["lease_until"] = lease

        return task

# Verify that this worker still owns the task
def owns_task(db, task):

    row = db.execute("""
        SELECT id FROM payment_outbox
        WHERE id = ?
        AND status = 'processing'
        AND attempts = ?
        AND lease_until = ?
    """, (
        task["id"],
        task["attempts"],
        task["lease_until"]
    )).fetchone()

    return row is not None

# Complete an approved or declined payment
def complete_payment(task, payment):

    approved = payment["status"] == "approved"

    reservation_status = (
        "confirmed" if approved else "failed"
    )

    outbox_status = (
        "completed" if approved else "failed"
    )

    with repo.database(write=True) as db:

        if not owns_task(db, task):
            return

        db.execute("""
            UPDATE reservations
            SET status = ?,
                payment_id = ?
            WHERE id = ?
        """, (
            reservation_status,
            payment["payment_id"],
            task["reservation_id"]
        ))

        db.execute("""
            UPDATE payment_outbox
            SET status = ?,
                lease_until = NULL,
                last_error = ?
            WHERE id = ?
        """, (
            outbox_status,
            None if approved else "Payment declined",
            task["id"]
        ))

    log_event(
        "payment_completed",
        task,
        outcome=reservation_status
    )

# Handle temporary or uncertain payment failures
def retry_payment(task, error):

    with repo.database(write=True) as db:

        if not owns_task(db, task):
            return

        # Maximum attempts reached
        if task["attempts"] >= PAYMENT_MAX_ATTEMPTS:

            db.execute("""
                UPDATE payment_outbox
                SET status = 'review',
                    lease_until = NULL,
                    last_error = ?
                WHERE id = ?
            """, (
                error,
                task["id"]
            ))

            db.execute("""
                UPDATE reservations
                SET status = 'payment_review'
                WHERE id = ?
            """, (task["reservation_id"],))

            log_event(
                "payment_review_required",
                task,
                error=error
            )

            return

        # Calculate exponential backoff
        delay = PAYMENT_RETRY_BASE_SECONDS * (
            2 ** (task["attempts"] - 1)
        )

        next_attempt = time.time() + delay

        db.execute("""
            UPDATE payment_outbox
            SET status = 'pending',
                next_attempt_at = ?,
                lease_until = NULL,
                last_error = ?
            WHERE id = ?
        """, (
            next_attempt,
            error,
            task["id"]
        ))

    log_event(
        "payment_retry_scheduled",
        task,
        delay_seconds=delay,
        error=error
    )

# Send a payment request to the independent provider
def process_payment(task):

    log_event("payment_started", task)

    try:

        response = requests.post(
            f"{PAYMENT_PROVIDER_URL}/payments",
            json=json.loads(task["payload"]),
            headers={
                "Idempotency-Key": task["idempotency_key"],
                "X-Correlation-ID": task["correlation_id"]
            },
            timeout=PAYMENT_TIMEOUT_SECONDS
        )

        response.raise_for_status()

        payment = response.json()

        # Validate the provider response
        if not isinstance(payment, dict):

            raise ValueError("Invalid payment response")

        if payment.get("status") not in (
            "approved",
            "declined"
        ):

            raise ValueError("Invalid payment status")

        if not isinstance(
            payment.get("payment_id"), str
        ) or not payment["payment_id"]:

            raise ValueError("Missing payment ID")

    except (requests.RequestException, ValueError) as error:

        retry_payment(
            task,
            type(error).__name__
        )

        return

    complete_payment(task, payment)

# Process one payment operation
def run_once():

    task = claim_task()

    if task is None:
        return False

    process_payment(task)

    return True

# Continuously process payment operations
def run_worker():

    repo.migrate()

    log.info("MetroSpot payment worker started")

    while True:

        try:

            processed = run_once()

            if not processed:
                time.sleep(WORKER_POLL_SECONDS)

        except Exception:

            log.exception("Unexpected worker error")

            time.sleep(WORKER_POLL_SECONDS)

# Start the worker as an independent process
if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s"
    )

    try:

        run_worker()

    except KeyboardInterrupt:

        log.info("MetroSpot payment worker stopped")