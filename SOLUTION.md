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

**HIPAA and PHI handling.** Patient names, dates of birth, and appointment records are Protected Health Information under HIPAA. In a real deployment this has implications at every layer: the database would need encryption at rest (AES-256) and the transport layer would need TLS everywhere, including the WebRTC stream and EHR API calls. Audit logging of every read and write to patient data would be mandatory. Call recordings and transcripts containing PHI would need to be stored in a HIPAA-eligible environment (AWS, Azure, or GCP each offer BAA-covered services), with retention and deletion policies that meet the minimum necessary standard. The LLM API calls themselves are a risk surface: sending raw PHI to a third-party provider requires a Business Associate Agreement with that provider. OpenAI and Anthropic both offer BAA-eligible enterprise tiers. Voice data is particularly sensitive because it can re-identify a patient even when stripped of obvious identifiers, so audio recordings would warrant stricter access controls and shorter retention than text transcripts.

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

Without `end_conversation`, the call hangs after the patient says goodbye: the pipeline stays open waiting for more audio. The tool gives the LLM an explicit exit, pushing a farewell phrase and then an `EndTaskFrame` to drain and close the pipeline cleanly.

All tools share a single lazy-initialised `httpx.AsyncClient`. Errors (404, 409) come back as structured dicts rather than exceptions so the LLM can respond naturally ("that slot is already taken, want to pick another?") rather than crashing the turn.

## Recording and transcription

Each session writes two files, both gitignored and keyed by a timestamp set at connect time.

For audio, I went with stereo (user left, bot right) rather than a mono mix so speaker tracks stay separable for any downstream analysis. The `AudioBufferProcessor` sits after `transport.output()` in the pipeline so it captures both directions, and recording starts explicitly on connect.

For the transcript, I just serialise `context.messages` on disconnect. It's the full OpenAI message list including tool calls and results, so you get complete observability into what the agent did without any custom accumulation logic.

## Testing

### Evaluation harness

This is a prototype that addresses evaluating whether the agent behaves correctly. The harness (`eval/`) runs a full scheduling conversation without any audio, Pipecat pipeline, or browser. Two OpenAI LLM calls alternate turns in a plain Python loop: the agent LLM receives the same system prompt and tool schemas as `bot.py` and calls the live EHR over HTTP, while the patient LLM receives a persona prompt and simulates a caller. The loop runs until the agent calls `end_conversation` or a turn limit is reached. Afterwards, a third LLM judge scores the full transcript against a rubric, and SQLAlchemy assertions verify the final DB state. Results are appended to `eval/report.json`.

```bash
# Prerequisites: EHR must be running
uv run uvicorn ehr.main:app --port 8000 --reload

# Run all scenarios
uv run -m eval
```

The first scenario is `book_new_patient`: the fixture deletes any pre-existing patient named Maria Garcia (DOB 1985-03-15), the patient LLM plays a first-time caller who wants a morning slot next week, and the DB assertions confirm that a patient row and a scheduled appointment row were created.

### Tradeoffs vs. testing bot.py directly

The alternative would be to connect a real audio client to the live Pipecat pipeline and test the full voice path. The two main tradeoffs against that approach are implementation complexity and harder-to-diagnose failures. Building a proper audio test client requires handling WebSocket lifecycle, audio format negotiation, STT synchronisation on both sides, and VAD timing, which is substantial work in itself. And once it is running, failures become harder to attribute: a booking flow can break because of a transcription error on a patient name rather than any fault in the LLM's logic, which makes it difficult to tell whether the agent is actually misbehaving. For validating that the agent collects the right information, confirms before acting, and handles EHR errors gracefully, text-only simulation is more reliable and much faster to iterate on.

That said, I am aware the full-pipeline approach is achievable and could be the right investment at a later stage; I even found a working example of it in the [pipecat-examples Twilio inbound chatbot](https://github.com/pipecat-ai/pipecat-examples/tree/main/twilio-chatbot/inbound), but it was outside the scope of this challenge.

## Cost analysis

The main per-call costs are ElevenLabs (STT + TTS) and OpenAI (LLM). SQLite and the FastAPI server are negligible.

| Component | Model / tier | Rough cost |
|-----------|-------------|-----------|
| ElevenLabs STT | Streaming (Flash) | ~$0.003 / min |
| ElevenLabs TTS | Streaming | ~$0.003 / min |
| OpenAI LLM | gpt-4o-mini | ~$0.001 / scheduling turn |

A typical scheduling call (2-4 min, 5-10 LLM turns) runs under **$0.02**. The dominant cost driver at scale would be TTS audio, since longer agent responses add up faster than LLM tokens.

## Latency
Because the pipeline follows a sequential flow (audio in → STT → LLM (+ function calls) → TTS → audio out), latency adds up across stages. The main levers to reduce it and their tradeoffs are:

**Turn detection** is the most impactful and least obvious lever. The current config uses `LocalSmartTurnAnalyzerV3` with Silero VAD at `stop_secs=0.2`, aggressive by design, but in a healthcare context cutting in too early means acting on incomplete information ("I want to cancel my appointment on... Tuesday"). Increasing to 0.5–0.8s improves accuracy at the cost of feeling slightly unresponsive. The right value should be tuned against real call recordings.

**Model choice.** GPT-4.1 sits in the practical sweet spot: fast enough for real-time conversation, capable enough for multi-step tool flows. A reasoning model would handle ambiguous cases more reliably but adds seconds of latency mid-call. A smaller model would be faster but more prone to mis-sequencing tool calls (e.g. booking without confirming first).

**Streaming TTS.** Pipecat pipes LLM tokens directly to ElevenLabs as they arrive, so the patient starts hearing the response before generation finishes. The tradeoff: if the LLM decides mid-stream to call a tool, it has to stop speaking mid-sentence. Prompt design can mitigate this by keeping tool calls away from the middle of a turn.

**Tool call chaining.** Some flows require sequential EHR calls (find patient → list slots → book). Each adds ~50–200ms locally, but against a real EHR (Epic, Athena) that becomes 500ms–2s per call. Parallelising independent calls where possible would help, though most scheduling flows are inherently sequential.

## Reliability

A clinic phone line needs to be available even when individual external providers have outages. There are three layers where fallback makes sense.

**AI service providers.** Each stage of the pipeline depends on an external provider: ElevenLabs for STT and TTS, OpenAI for the LLM. If any one of them becomes unavailable mid-call, the bot goes silent, which is worse than never picking up. Pipecat's service abstraction helps here because each provider implements the same frame interface, so a circuit breaker can swap in an alternative at runtime. For the LLM, the system prompt, tool definitions, and conversation history are all provider-agnostic, so switching to Anthropic mid-session just means re-instantiating the service with the current context. For STT and TTS, alternatives like Deepgram or Azure Cognitive Speech can be slotted in without changing the pipeline topology.

**Telephony.** If the primary telephony provider (say Telnyx) has a regional outage, the PSTN number can be rerouted to a fallback (say Twilio) within seconds via a DNS or SIP redirect, and the Pipecat transport layer swaps accordingly. Both providers support SIP trunking and WebRTC, so the pipeline wiring does not change.

**Human fallback.** Both of the above cover transient technical failures, but there are situations no automated fallback can handle: a confused patient, an edge case the LLM mishandles repeatedly, or a complete multi-provider outage. There should always be at least one human administrator available to take over a call. When the bot detects it is failing to make progress (for example, the same intent is not resolved after several turns, or an unhandled exception occurs) it should transfer the call to the on-call human rather than leaving the patient hanging.
