
"""Request and response models for the MetroSpot API."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
)


SpotID = Annotated[
    str, StringConstraints(pattern=r"^P-[0-9]{3}$")
]

DriverName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]

Plate = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]{0,15}$",
    ),
]


# Supported mock payment methods
PaymentMethod = Literal[
    "approved",
    "declined"
]


# Possible reservation states
ReservationStatus = Literal[
    "pending_payment",
    "processing_payment",
    "confirmed",
    "failed",
    "expired",
    "payment_review"
]


class Login(BaseModel):

    model_config = ConfigDict(extra="forbid")

    username: Literal["driver-a", "driver-b"]

    password: Annotated[
        str, StringConstraints(min_length=1, max_length=200)
    ]


class Token(BaseModel):

    access_token: str
    token_type: Literal["bearer"] = "bearer"


class ReservationInput(BaseModel):

    model_config = ConfigDict(extra="forbid")

    spot_id: SpotID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    driver_name: DriverName
    license_plate: Plate

    payment_method: PaymentMethod = "approved"


class Reservation(BaseModel):

    id: UUID
    spot_id: SpotID
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    status: ReservationStatus = "confirmed"

    amount_cents: int = 0
    expires_at: AwareDatetime | None = None
    payment_id: str | None = None


class Spot(BaseModel):

    id: SpotID


class Error(BaseModel):

    code: str
    message: str
    correlation_id: str
