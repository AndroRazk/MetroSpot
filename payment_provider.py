
"""Independent mock payment provider for MetroSpot."""

import os
import sqlite3

from contextlib import closing, asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


DB_PATH = os.getenv(
    "PAYMENT_DB_PATH",
    "payments.sqlite3"
)


# Payment request model
class PaymentInput(BaseModel):

    reservation_id: str
    amount_cents: int = Field(gt=0)

    payment_method: Literal[
        "approved",
        "declined"
    ] = "approved"


# Initialize the payment database
@asynccontextmanager
async def lifespan(app):

    with closing(sqlite3.connect(DB_PATH)) as db:

        with db:

            db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    request_key TEXT UNIQUE NOT NULL,
                    reservation_id TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    payment_method TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)

    yield


app = FastAPI(
    title="Mock Payment Provider",
    version="1.0.0",
    lifespan=lifespan,
    debug=False
)


# Health check
@app.get("/health")
def health():

    return {"status": "ok"}


# Process a payment
@app.post("/payments")
def process_payment(
    request: PaymentInput,
    request_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=120
        )
    ]
):

    with closing(
        sqlite3.connect(DB_PATH, timeout=10)
    ) as db:

        db.row_factory = sqlite3.Row

        with db:

            db.execute("BEGIN IMMEDIATE")

            # Check whether this payment already exists
            existing = db.execute("""
                SELECT * FROM payments
                WHERE request_key = ?
            """, (request_key,)).fetchone()

            if existing:

                # Reject the same key with different data
                if (
                    existing["reservation_id"]
                    != request.reservation_id

                    or existing["amount_cents"]
                    != request.amount_cents

                    or existing["payment_method"]
                    != request.payment_method
                ):

                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key conflict"
                    )

                return {
                    "payment_id": existing["id"],
                    "status": existing["status"]
                }

            # Simulate the payment outcome
            if request.payment_method == "declined":
                status = "declined"
            else:
                status = "approved"

            payment_id = str(uuid4())

            # Persist the payment result
            db.execute("""
                INSERT INTO payments (
                    id,
                    request_key,
                    reservation_id,
                    amount_cents,
                    payment_method,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                payment_id,
                request_key,
                request.reservation_id,
                request.amount_cents,
                request.payment_method,
                status
            ))

            return {
                "payment_id": payment_id,
                "status": status
            }


# Start the independent payment service
if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8001
    )
