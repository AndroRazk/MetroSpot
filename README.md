
# MetroSpot

SOEN 387 – Web-Based Enterprise Application Design  
Fall 2026  
Founder: Andro Razk

## Project Overview

MetroSpot is a parking reservation service that allows drivers to search for and reserve numbered parking spots near a metro station. Drivers can select their arrival and departure times, book a parking spot, complete a simulated payment and view their reservations.

The system prevents double-booking and processes payments asynchronously through a separate payment provider.

## Technology Stack

- Python
- FastAPI
- SQLite
- JWT authentication
- HTML, CSS and JavaScript
- OpenAPI 3.x
- Git and GitHub

## Main Features

- Driver authentication and reservation ownership
- Parking spot availability search
- Protection against concurrent double-booking
- Asynchronous payment processing
- Idempotent reservation and payment requests
- Automatic payment retries with exponential backoff
- Release of parking spots after declined payments
- Expiration of unpaid reservations
- Durable database and schema migrations
- Reservation history and status filtering
- Health checks and structured logging
- Published OpenAPI contract

## Project Structure

| File | Responsibility |
|---|---|
| app.py | API interface and request handling |
| service.py | Business rules and reservation logic |
| repository.py | Database operations and migrations |
| models.py | Request and response models |
| auth.py | Authentication and JWT validation |
| config.py | Environment configuration |
| payment_provider.py | Independent simulated payment service |
| worker.py | Asynchronous payment processing |
| reconcile.py | Manual recovery of unresolved payments |
| index.html | Frontend for testing |
| openapi.yaml | Published API contract |
| demo.py | Final service demonstration |
| conformance_test.py | API contract testing |
| compatibility_test.py | Backward compatibility testing |

## Installation

Open a terminal in the MetroSpot project directory.

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

On Windows, activate the virtual environment using:

```powershell
.\venv\Scripts\Activate.ps1
```

## Running the Application

The application requires three running processes: the payment provider, the MetroSpot API and the payment worker.

### Terminal 1 – Payment Provider

```bash
source venv/bin/activate
python3 payment_provider.py
```

The payment provider runs on port 8001.

### Terminal 2 – MetroSpot API

```bash
source venv/bin/activate

export JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export DRIVER_A_PASSWORD="demo-a"
export DRIVER_B_PASSWORD="demo-b"
export DB_PATH=metrospot.sqlite3

python3 app.py
```

The passwords above are for local demonstration purposes only.

### Terminal 3 – Payment Worker

```bash
source venv/bin/activate
export DB_PATH=metrospot.sqlite3

python3 worker.py
```

The API and worker must use the same database.

Database tables and migrations are created automatically.

## Using MetroSpot

Open the following address in a browser:

http://127.0.0.1:8000

The two demonstration accounts are:

| Username | Password |
|---|---|
| driver-a | demo-a |
| driver-b | demo-b |

After signing in, select an arrival and departure time, search for an available parking spot and submit a reservation.

The frontend allows approved and declined payments to be simulated for testing.

## API Documentation

Interactive API documentation:

http://127.0.0.1:8000/docs

Published OpenAPI contract:

http://127.0.0.1:8000/openapi.json

The repository also contains previous contract versions in the contracts directory to demonstrate backward compatibility.

## Testing

With all three processes running, open another terminal:

```bash
source venv/bin/activate

python3 demo.py
python3 conformance_test.py
python3 compatibility_test.py
```

These scripts demonstrate concurrency protection, authentication, authorization, idempotency, asynchronous payments, compensation, API conformance and backward compatibility.

The previous API contract can also be tested against the current service:

```bash
METROSPOT_CONTRACT=contracts/openapi-v0.2.0.yaml python3 conformance_test.py
```

## Health Checks

Liveness:

http://127.0.0.1:8000/health/live

Readiness:

http://127.0.0.1:8000/health/ready

The service generates structured logs with correlation identifiers to help track requests and payment operations.

## Payment Failure Handling

If the payment provider is temporarily unavailable, the worker retries the payment using exponential backoff.

A declined payment marks the reservation as failed and releases the parking spot.

If the payment outcome remains uncertain after the maximum number of attempts, the reservation is marked for manual review.

To list unresolved payments:

```bash
export DB_PATH=metrospot.sqlite3
python3 reconcile.py list
```

To retry an unresolved payment:

```bash
python3 reconcile.py retry RESERVATION_ID
```

## Limitations

MetroSpot is an academic project intended to demonstrate enterprise application design principles. Payments are simulated, parking spots are predefined and driver accounts are configured through environment variables.

The application is designed for local demonstration rather than commercial deployment.
