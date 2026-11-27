
"""Resolve uncertain payment operations in MetroSpot."""

import argparse
import json
import time

import repository as repo


# Retrieve payment operations requiring review
def list_review_payments():

    with repo.database() as db:

        rows = db.execute("""
            SELECT
                reservation_id,
                correlation_id,
                attempts,
                last_error
            FROM payment_outbox
            WHERE status = 'review'
            ORDER BY created_at
        """).fetchall()

        return [dict(row) for row in rows]


# Requeue an uncertain payment operation
def reconcile_payment(reservation_id):

    with repo.database(write=True) as db:

        task = db.execute("""
            SELECT *
            FROM payment_outbox
            WHERE reservation_id = ?
            AND status = 'review'
        """, (reservation_id,)).fetchone()

        if task is None:
            return False

        reservation = db.execute("""
            SELECT status
            FROM reservations
            WHERE id = ?
        """, (reservation_id,)).fetchone()

        if (
            reservation is None
            or reservation["status"] != "payment_review"
        ):
            return False

        # Requeue the original payment operation
        db.execute("""
            UPDATE payment_outbox
            SET status = 'pending',
                attempts = 0,
                next_attempt_at = ?,
                lease_until = NULL,
                last_error = NULL
            WHERE id = ?
        """, (
            time.time(),
            task["id"]
        ))

        # Keep the parking spot protected
        db.execute("""
            UPDATE reservations
            SET status = 'processing_payment'
            WHERE id = ?
        """, (reservation_id,))

    return True


# Command-line interface
def main():

    parser = argparse.ArgumentParser(
        description="MetroSpot payment reconciliation"
    )

    parser.add_argument(
        "action",
        choices=["list", "retry"]
    )

    parser.add_argument(
        "reservation_id",
        nargs="?"
    )

    args = parser.parse_args()

    repo.migrate()

    # Display uncertain payments
    if args.action == "list":

        payments = list_review_payments()

        print(json.dumps(payments, indent=4))

    # Retry a specific uncertain payment
    elif args.action == "retry":

        if not args.reservation_id:

            parser.error(
                "A reservation ID is required."
            )

        if reconcile_payment(args.reservation_id):

            print("Payment successfully requeued.")

        else:

            print("No matching payment requires review.")

            raise SystemExit(1)


if __name__ == "__main__":

    main()
