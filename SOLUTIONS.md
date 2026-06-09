# Solution

## EHR HTTP API

FastAPI + SQLAlchemy over SQLite, chosen for simplicity and adequacy to the challenge. FastAPI gives you Swagger for free (invaluable when debugging the agent), SQLAlchemy handles migrations implicitly via `create_all`, and SQLite means the whole persistence layer is a single file with no ops overhead. In a real clinic system you'd swap SQLite for Postgres, but the ORM layer makes that a one-line change.

### Data model

Three tables:

| Table | Key columns |
|-------|-------------|
| `patients` | `id`, `first_name`, `last_name`, `date_of_birth`, `phone`, `email`, `created_at` |
| `appointment_slots` | `id`, `starts_at` (datetime), `duration_minutes` (default 30) |
| `appointments` | `id`, `patient_id` → patients, `slot_id` → appointment_slots (unique), `status` (scheduled/cancelled), `notes`, `created_at` |

The slot/appointment split deserves explanation because it's the most consequential design call: `AppointmentSlot` is just a time window the clinic offers; `Appointment` is a patient claiming one. This means availability queries are a simple `NOT IN (scheduled appointments)` subquery, with no booking state embedded in the slot row. Cancellation is a soft-delete on the appointment row, which immediately frees the slot without touching it. Clean separation, easy to reason about.

Slots are pre-seeded at startup (Mon-Fri, 9 AM-5 PM, 30-min blocks, next 30 days) rather than computed dynamically. The tradeoff is a fixed window. A real clinic would want a rolling seed job, but this simplifies every availability query to a filter.

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/patients` | Register a new patient; 409 if same name + DOB exists |
| `GET` | `/patients` | Look up a patient by name + DOB |
| `GET` | `/slots` | Available slots for a date range; excludes already-booked ones |
| `GET` | `/patients/{id}/appointments` | Scheduled appointments for a patient (needed before cancelling) |
| `POST` | `/appointments` | Book a slot; 409 if slot already taken |
| `DELETE` | `/appointments/{id}` | Cancel, soft delete, slot becomes available again |

### Things I'd fix in production

**Race condition on double-booking.** The SELECT-then-INSERT has a TOCTOU window: two concurrent requests for the same slot can both pass the conflict check before either commits. The `slot_id` unique constraint means the second commit will at least fail with an integrity error rather than corrupt data, but the user gets a 500 instead of a clean 409. Fix with `SELECT FOR UPDATE` or a unique partial index on `(slot_id) WHERE status='scheduled'` plus a retry.

**Exact-match patient lookup.** Requiring exact `first_name`, `last_name`, `date_of_birth` is fragile in a voice context. STT will produce "Jon" for "John", "Smyth" for "Smith". The right fix is fuzzy matching (phonetic similarity or edit distance) in the lookup, or a normalisation step in the agent before the query.

---

## Voice Agent

### Why Pipecat

Pipecat was already the designated framework here, but it's a reasonable choice for this problem. The pipeline abstraction, a linear chain of frames flowing through processors, maps well to real-time voice: audio in, STT, LLM, TTS, audio out. Each stage is decoupled and swappable. The alternative would be hand-rolling the same thing with raw WebSockets and async queues, which isn't hard but adds a lot of glue code that Pipecat handles for you.

### EHR tools

Six async functions registered with the LLM via `register_direct_function`. Pipecat auto-extracts name, description, and parameter schema from each function's signature and docstring, so the docstring is load-bearing, not decoration.

| Function | EHR call | Purpose |
|---|---|---|
| `find_patient` | `GET /patients` | Look up patient by name + DOB |
| `register_patient` | `POST /patients` | Register a new patient |
| `get_available_slots` | `GET /slots` | List open slots for a date range |
| `book_appointment` | `POST /appointments` | Book a slot |
| `get_patient_appointments` | `GET /patients/{id}/appointments` | Fetch a patient's scheduled appointments, needed so the agent can present them before cancelling rather than expecting the caller to know their appointment ID |
| `cancel_appointment` | `DELETE /appointments/{id}` | Cancel by ID |
| `end_conversation` | (no EHR call) | Terminate the pipeline gracefully once the conversation is done |

The cancel flow specifically motivated `get_patient_appointments`: without it, `cancel_appointment` required an appointment ID the caller has no way of knowing. Now the agent identifies the patient, fetches their appointments, reads them back, confirms which one to cancel, then calls `cancel_appointment` with the right ID.

`end_conversation` exists because without it the call just... hangs. The patient says goodbye, the LLM responds, and the pipeline sits there waiting for more audio. The fix is to give the LLM an explicit exit: when it detects the conversation is done, it calls `end_conversation`, which pushes a goodbye `TTSSpeakFrame` followed by an `EndTaskFrame` upstream. Pipecat converts that into an `EndFrame` at the pipeline source, which drains downstream through every processor for a clean shutdown.

All tools share a single lazy-initialised `httpx.AsyncClient`. Errors (404, 409) come back as structured dicts rather than exceptions so the LLM can respond naturally ("that slot is already taken, want to pick another?") rather than crashing the turn.

---

## Cost analysis

The main per-call costs are ElevenLabs (STT + TTS) and OpenAI (LLM). SQLite and the FastAPI server are negligible.

| Component | Model / tier | Rough cost |
|-----------|-------------|-----------|
| ElevenLabs STT | Streaming (Flash) | ~$0.003 / min |
| ElevenLabs TTS | Streaming | ~$0.003 / min |
| OpenAI LLM | gpt-4o-mini | ~$0.001 / scheduling turn |

A typical scheduling call (2-4 min, 5-10 LLM turns) runs under **$0.02**. The dominant cost driver at scale would be TTS audio, since longer agent responses add up faster than LLM tokens.
