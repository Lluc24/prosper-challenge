# Solution Overview

## EHR HTTP API

Built with **FastAPI** (zero-boilerplate REST + auto Swagger), **SQLAlchemy** ORM over **SQLite** (file-based, zero-ops, survives restarts) — all three chosen for simplicity given the scope.

### Data model

Three tables:

| Table | Key columns |
|-------|-------------|
| `patients` | `id`, `first_name`, `last_name`, `date_of_birth`, `phone`, `email`, `created_at` |
| `appointment_slots` | `id`, `starts_at` (datetime), `duration_minutes` (default 30) |
| `appointments` | `id`, `patient_id` → patients, `slot_id` → appointment_slots (unique), `status` (scheduled/cancelled), `notes`, `created_at` |

`appointment_slots` holds every possible time window the clinic offers. `appointments` links a patient to a slot; a slot is considered available if no appointment with `status=scheduled` references it.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/patients` | Register a new patient; 409 if same name + DOB already exists |
| `GET` | `/patients` | Look up a patient by `first_name`, `last_name`, `date_of_birth`; 404 if not found |
| `GET` | `/slots` | List available slots for a `start_date` (required) and optional `end_date`; excludes booked slots |
| `POST` | `/appointments` | Book a slot for a patient; 409 if slot already taken |
| `DELETE` | `/appointments/{id}` | Cancel an appointment (soft-delete — sets `status=cancelled`, freeing the slot) |

Interactive docs available at `http://localhost:8000/docs` when running.

### Design decisions

**Pre-seeded slots vs on-the-fly generation.** Slots are generated once at startup (Mon–Fri, 9 AM–5 PM, 30-min intervals, next 30 days) rather than computed dynamically on each request. This simplifies availability queries to a plain `NOT IN booked_slot_ids` filter and makes the API predictable. The tradeoff is a fixed 30-day window — a real clinic would need a rolling seed job or a schedule-template approach.

**Slot/Appointment separation.** `AppointmentSlot` (a time window) and `Appointment` (a patient booking a slot) are distinct tables. This lets `GET /slots` return only genuinely free times via a single subquery filter, without embedding booking state inside the slot row. It also makes cancellation trivially a soft-delete on the appointment — the slot row is untouched and becomes available again immediately.

**Race condition on double-booking.** The current check (`SELECT` then `INSERT`) has a TOCTOU window: two concurrent requests for the same slot can both pass the conflict check before either commits. In production this is fixed with `SELECT FOR UPDATE` (pessimistic locking) or a unique constraint on `(slot_id, status='scheduled')` combined with a retry loop (optimistic locking). The `slot_id` unique constraint on `appointments` partially mitigates this — the second commit will fail with an integrity error — but the error surface to the user is worse than a clean 409.

**Exact-match patient lookup.** `GET /patients` requires exact `first_name`, `last_name`, and `date_of_birth`. In a voice context this is fragile: speech-to-text errors ("Jon" vs "John", "Smith" vs "Smyth") will produce false 404s. A fuzzy-match approach (phonetic similarity, edit distance, or a dedicated name-normalisation step in the agent) is the right fix and will be addressed in a future iteration.
