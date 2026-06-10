"""Shared EHR client logic and tool schemas — no Pipecat dependency.

Used by the eval harness. bot.py keeps its own copies of these functions
because they require FunctionCallParams wrappers for the Pipecat pipeline.
"""

import os
from datetime import datetime

import httpx
from loguru import logger

EHR_URL = os.environ.get("EHR_URL", "http://localhost:8000")

_ehr_client: httpx.AsyncClient | None = None


def get_ehr_client() -> httpx.AsyncClient:
    global _ehr_client
    if _ehr_client is None:
        _ehr_client = httpx.AsyncClient(base_url=EHR_URL, timeout=10.0)
    return _ehr_client


def get_system_prompt() -> str:
    """Return the agent system prompt with current timestamp."""
    return (
        f"You are a scheduling assistant for Prosper Health clinic. Your job is to help patients "
        f"look up, book, and cancel appointments over the phone. Today's date and time is "
        f"{datetime.now().strftime('%Y-%m-%d, %H:%M:%S')} (YYYY-MM-DD format)\n\n"
        "Guidelines:\n"
        "- Always greet the patient warmly and introduce yourself as the Prosper Health scheduling assistant.\n"
        "- Before booking, identify the patient: ask for their first name, last name, and date of birth. "
        "Before using identify_patient, spell the name letter by letter and repeat DOB to make sure "
        "they are correct. If the patient is not found, offer to register them.\n"
        "- When showing available slots, present them in a friendly way (e.g. \"Monday the 9th at 10 AM\"). Make "
        "sure to present only available slots at least 30 minutes post current time.\n"
        "- To cancel, first identify the patient with find_patient, then call get_patient_appointments to "
        "show them their scheduled appointments, then confirm which one to cancel before calling "
        "cancel_appointment with the appointment ID.\n"
        "- Always confirm the details with the patient before calling book_appointment or cancel_appointment.\n"
        "- Keep responses concise and conversational — this is a voice call.\n"
        "- When the conversation has naturally concluded (task done, patient says goodbye), call "
        "end_conversation so the call terminates cleanly.\n"
    )


# ---------------------------------------------------------------------------
# Pure EHR functions — same HTTP logic as bot.py, return dict directly
# ---------------------------------------------------------------------------


async def find_patient(first_name: str, last_name: str, date_of_birth: str) -> dict:
    """Look up an existing patient by name and date of birth."""
    client = get_ehr_client()
    resp = await client.get(
        "/patients",
        params={"first_name": first_name, "last_name": last_name, "date_of_birth": date_of_birth},
    )
    if resp.status_code == 404:
        logger.info(f"Patient not found: {first_name} {last_name} {date_of_birth}")
        return {"found": False}
    data = resp.json()
    logger.info(f"Patient found: id={data['id']} name={first_name} {last_name}")
    return {"found": True, **data}


async def register_patient(
    first_name: str,
    last_name: str,
    date_of_birth: str,
    phone: str = "",
    email: str = "",
) -> dict:
    """Register a new patient in the system."""
    client = get_ehr_client()
    payload: dict = {
        "first_name": first_name,
        "last_name": last_name,
        "date_of_birth": date_of_birth,
    }
    if phone:
        payload["phone"] = phone
    if email:
        payload["email"] = email
    resp = await client.post("/patients", json=payload)
    data = resp.json()
    if resp.status_code == 409:
        logger.info(f"Patient already exists: {first_name} {last_name}")
        return {"error": "patient_already_exists", **data}
    logger.info(f"Patient registered: id={data['id']} name={first_name} {last_name}")
    return data


async def get_available_slots(start_date: str, end_date: str) -> dict:
    """List available appointment slots for a date range."""
    client = get_ehr_client()
    resp = await client.get("/slots", params={"start_date": start_date, "end_date": end_date})
    slots = resp.json()
    logger.info(f"Available slots from {start_date} to {end_date}: {len(slots)} returned")
    return {"slots": slots}


async def book_appointment(patient_id: int, slot_id: int, notes: str = "") -> dict:
    """Book an appointment slot for a patient."""
    client = get_ehr_client()
    payload: dict = {"patient_id": patient_id, "slot_id": slot_id}
    if notes:
        payload["notes"] = notes
    resp = await client.post("/appointments", json=payload)
    data = resp.json()
    if resp.status_code == 409:
        logger.info(f"Slot {slot_id} already booked")
        return {"error": "slot_already_booked", **data}
    logger.info(f"Appointment booked: id={data['id']} patient={patient_id} slot={slot_id}")
    return data


async def get_patient_appointments(patient_id: int) -> dict:
    """List all scheduled (non-cancelled) appointments for a patient."""
    client = get_ehr_client()
    resp = await client.get(f"/patients/{patient_id}/appointments")
    if resp.status_code == 404:
        logger.info(f"Patient {patient_id} not found when fetching appointments")
        return {"error": "patient_not_found"}
    appointments = resp.json()
    logger.info(f"Appointments for patient {patient_id}: {len(appointments)} scheduled")
    return {"appointments": appointments}


async def cancel_appointment(appointment_id: int) -> dict:
    """Cancel an existing appointment."""
    client = get_ehr_client()
    resp = await client.delete(f"/appointments/{appointment_id}")
    data = resp.json()
    if resp.status_code == 404:
        logger.info(f"Appointment {appointment_id} not found for cancellation")
        return {"error": "appointment_not_found"}
    if resp.status_code == 409:
        logger.info(f"Appointment {appointment_id} already cancelled")
        return {"error": "already_cancelled", **data}
    logger.info(f"Appointment cancelled: id={appointment_id}")
    return data


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_TOOL_DISPATCH = {
    "find_patient": find_patient,
    "register_patient": register_patient,
    "get_available_slots": get_available_slots,
    "book_appointment": book_appointment,
    "get_patient_appointments": get_patient_appointments,
    "cancel_appointment": cancel_appointment,
}


async def call_ehr_tool(name: str, args: dict) -> dict:
    """Dispatch a tool call by name. end_conversation must be intercepted by the caller."""
    fn = _TOOL_DISPATCH.get(name)
    if fn is None:
        logger.warning(f"Unknown tool requested: {name}")
        return {"error": f"unknown_tool: {name}"}
    return await fn(**args)


# ---------------------------------------------------------------------------
# OpenAI tool schemas
# ---------------------------------------------------------------------------

OPENAI_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "find_patient",
            "description": "Look up an existing patient by name and date of birth.",
            "parameters": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string", "description": "Patient's first name."},
                    "last_name": {"type": "string", "description": "Patient's last name."},
                    "date_of_birth": {
                        "type": "string",
                        "description": "Patient's date of birth in YYYY-MM-DD format.",
                    },
                },
                "required": ["first_name", "last_name", "date_of_birth"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_patient",
            "description": "Register a new patient in the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string", "description": "Patient's first name."},
                    "last_name": {"type": "string", "description": "Patient's last name."},
                    "date_of_birth": {
                        "type": "string",
                        "description": "Patient's date of birth in YYYY-MM-DD format.",
                    },
                    "phone": {
                        "type": "string",
                        "description": "Patient's phone number (optional).",
                    },
                    "email": {
                        "type": "string",
                        "description": "Patient's email address (optional).",
                    },
                },
                "required": ["first_name", "last_name", "date_of_birth"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": "List available appointment slots for a date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start of the date range in YYYY-MM-DD format.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End of the date range in YYYY-MM-DD format.",
                    },
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book an appointment slot for a patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {
                        "type": "integer",
                        "description": "The patient's ID from find_patient or register_patient.",
                    },
                    "slot_id": {
                        "type": "integer",
                        "description": "The slot ID from get_available_slots.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes for the appointment.",
                    },
                },
                "required": ["patient_id", "slot_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient_appointments",
            "description": "List all scheduled (non-cancelled) appointments for a patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {
                        "type": "integer",
                        "description": "The patient's ID from find_patient or register_patient.",
                    },
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an existing appointment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type": "integer",
                        "description": "The appointment ID to cancel.",
                    },
                },
                "required": ["appointment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_conversation",
            "description": (
                "End the conversation gracefully once the patient's request has been "
                "handled and they have said goodbye."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
