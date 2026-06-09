# EHR Service

HTTP API for patient and appointment management. Backed by SQLite (`ehr.db` at the project root).

## Run

```bash
# From the project root
uv run uvicorn ehr.main:app --port 8000 --reload
```

On first boot the service creates the database schema and seeds ~350 business-hours appointment slots (Mon–Fri, 9 AM–5 PM, 30-min intervals, next 30 days).

Interactive docs: **http://localhost:8000/docs**

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/patients` | Register a new patient |
| `GET` | `/patients` | Find a patient by `first_name`, `last_name`, `date_of_birth` |
| `GET` | `/slots` | List available slots for `start_date` (and optional `end_date`) |
| `POST` | `/appointments` | Book a slot for a patient |
| `DELETE` | `/appointments/{id}` | Cancel an appointment |
