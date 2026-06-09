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

---

## Voice Agent — EHR Integration

### What was added

The bot now acts as a full scheduling assistant. Two changes were made to `bot.py`:

1. **System prompt** — replaced the generic "friendly assistant" prompt with a clinic-specific one that instructs the LLM to identify itself as the Prosper Health scheduling assistant, collect patient details before booking, confirm actions before executing them, and present slots in a human-friendly format.

2. **EHR tool functions** — five async functions registered with the LLM via `register_direct_function` (pipecat's direct-function API, which auto-extracts name/description/parameters from the function signature and docstring):

| Function | EHR endpoint | Purpose |
|---|---|---|
| `find_patient` | `GET /patients` | Look up a patient by name + DOB |
| `register_patient` | `POST /patients` | Register a new patient |
| `get_available_slots` | `GET /slots` | List open slots for a date range |
| `book_appointment` | `POST /appointments` | Book a slot for a patient |
| `cancel_appointment` | `DELETE /appointments/{id}` | Cancel an existing appointment |

All functions share a single `httpx.AsyncClient` (lazy-initialised) pointed at `EHR_URL` (from `.env`). Each logs meaningful outcomes (patient IDs, slot counts, errors) and calls `params.result_callback` with the result so pipecat can continue the conversation turn.

### Design decisions

**`register_direct_function` + `ToolsSchema`.** Pipecat 0.0.100 requires both: `register_direct_function` wires the handler so pipecat can dispatch function calls at runtime; `ToolsSchema(standard_tools=EHR_TOOLS)` passed to `LLMContext` makes the tool definitions available to the LLM on each inference request. Omitting either breaks function calling silently (handler not called, or tools not sent to OpenAI, respectively).

**Shared `httpx.AsyncClient`.** A single client is reused across tool calls to benefit from HTTP keep-alive and connection pooling. It is lazily created on first use so the module-level `load_dotenv` has already run before `EHR_URL` is read.

**Soft error returns.** Tool functions never raise — EHR errors (404, 409) are returned as structured dicts (`{"error": "..."}`) so the LLM can reason about them and respond to the patient naturally (e.g. "that slot is already taken, would you like another?") rather than crashing the turn.
