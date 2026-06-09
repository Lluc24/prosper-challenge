#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat Quickstart Example.

The example runs a simple voice AI bot that you can connect to using your
browser and speak with it. You can also deploy this bot to Pipecat Cloud.

Required AI services:
- ElevenLabs (Speech-to-Text and Text-to-Speech)
- OpenAI (LLM)

Run the bot using::

    uv run bot.py
"""

import os

import httpx
from dotenv import load_dotenv
from loguru import logger
from datetime import datetime

print("🚀 Starting Pipecat bot...")
print("⏳ Loading models and imports (20 seconds, first run only)\n")

logger.info("Loading Local Smart Turn Analyzer V3...")
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

logger.info("✅ Local Smart Turn Analyzer V3 loaded")
logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer

logger.info("✅ Silero VAD model loaded")

from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame

logger.info("Loading pipeline components...")
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

logger.info("✅ All components loaded successfully!")

load_dotenv(override=True)

EHR_URL = os.environ.get("EHR_URL", "http://localhost:8000")

SYSTEM_PROMPT = f"""\
You are a scheduling assistant for Prosper Health clinic. Your job is to help patients look up, \
book, and cancel appointments over the phone.

Guidelines:
- Always greet the patient warmly and introduce yourself as the Prosper Health scheduling assistant.
- Before booking, identify the patient: ask for their first name, last name, and date of birth \
  (format YYYY-MM-DD). Use find_patient first; if not found, offer to register them.
- When showing available slots, present them in a friendly way (e.g. "Monday the 9th at 10 AM").
- To cancel, first identify the patient with find_patient, then call get_patient_appointments to \
  show them their scheduled appointments, then confirm which one to cancel before calling \
  cancel_appointment with the appointment ID.
- Always confirm the details with the patient before calling book_appointment or cancel_appointment.
- Keep responses concise and conversational — this is a voice call.
- Today's date and time is {datetime.now().strftime("%Y-%m-%d, %H:%M:%S")} (YYYY-MM-DD format).
"""

_ehr_client: httpx.AsyncClient | None = None


def get_ehr_client() -> httpx.AsyncClient:
    global _ehr_client
    if _ehr_client is None:
        _ehr_client = httpx.AsyncClient(base_url=EHR_URL, timeout=10.0)
    return _ehr_client


async def find_patient(
    params: FunctionCallParams, first_name: str, last_name: str, date_of_birth: str
):
    """Look up an existing patient by name and date of birth.

    Args:
        first_name: Patient's first name.
        last_name: Patient's last name.
        date_of_birth: Patient's date of birth in YYYY-MM-DD format.
    """
    client = get_ehr_client()
    resp = await client.get(
        "/patients",
        params={"first_name": first_name, "last_name": last_name, "date_of_birth": date_of_birth},
    )
    if resp.status_code == 404:
        logger.info(f"Patient not found: {first_name} {last_name} {date_of_birth}")
        await params.result_callback({"found": False})
    else:
        data = resp.json()
        logger.info(f"Patient found: id={data['id']} name={first_name} {last_name}")
        await params.result_callback({"found": True, **data})


async def register_patient(
    params: FunctionCallParams,
    first_name: str,
    last_name: str,
    date_of_birth: str,
    phone: str = "",
    email: str = "",
):
    """Register a new patient in the system.

    Args:
        first_name: Patient's first name.
        last_name: Patient's last name.
        date_of_birth: Patient's date of birth in YYYY-MM-DD format.
        phone: Patient's phone number (optional).
        email: Patient's email address (optional).
    """
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
        await params.result_callback({"error": "patient_already_exists", **data})
    else:
        logger.info(f"Patient registered: id={data['id']} name={first_name} {last_name}")
        await params.result_callback(data)


async def get_available_slots(params: FunctionCallParams, start_date: str, end_date: str):
    """List available appointment slots for a date range.

    Args:
        start_date: Start of the date range in YYYY-MM-DD format.
        end_date: End of the date range in YYYY-MM-DD format.
    """
    client = get_ehr_client()
    resp = await client.get("/slots", params={"start_date": start_date, "end_date": end_date})
    slots = resp.json()
    logger.info(f"Available slots from {start_date} to {end_date}: {len(slots)} returned")
    await params.result_callback({"slots": slots})


async def book_appointment(
    params: FunctionCallParams, patient_id: int, slot_id: int, notes: str = ""
):
    """Book an appointment slot for a patient.

    Args:
        patient_id: The patient's ID from find_patient or register_patient.
        slot_id: The slot ID from get_available_slots.
        notes: Optional notes for the appointment.
    """
    client = get_ehr_client()
    payload: dict = {"patient_id": patient_id, "slot_id": slot_id}
    if notes:
        payload["notes"] = notes
    resp = await client.post("/appointments", json=payload)
    data = resp.json()
    if resp.status_code == 409:
        logger.info(f"Slot {slot_id} already booked")
        await params.result_callback({"error": "slot_already_booked", **data})
    else:
        logger.info(f"Appointment booked: id={data['id']} patient={patient_id} slot={slot_id}")
        await params.result_callback(data)


async def get_patient_appointments(params: FunctionCallParams, patient_id: int):
    """List all scheduled (non-cancelled) appointments for a patient.

    Args:
        patient_id: The patient's ID from find_patient or register_patient.
    """
    client = get_ehr_client()
    resp = await client.get(f"/patients/{patient_id}/appointments")
    if resp.status_code == 404:
        logger.info(f"Patient {patient_id} not found when fetching appointments")
        await params.result_callback({"error": "patient_not_found"})
    else:
        appointments = resp.json()
        logger.info(f"Appointments for patient {patient_id}: {len(appointments)} scheduled")
        await params.result_callback({"appointments": appointments})


async def cancel_appointment(params: FunctionCallParams, appointment_id: int):
    """Cancel an existing appointment.

    Args:
        appointment_id: The appointment ID to cancel.
    """
    client = get_ehr_client()
    resp = await client.delete(f"/appointments/{appointment_id}")
    data = resp.json()
    if resp.status_code == 404:
        logger.info(f"Appointment {appointment_id} not found for cancellation")
        await params.result_callback({"error": "appointment_not_found"})
    elif resp.status_code == 409:
        logger.info(f"Appointment {appointment_id} already cancelled")
        await params.result_callback({"error": "already_cancelled", **data})
    else:
        logger.info(f"Appointment cancelled: id={appointment_id}")
        await params.result_callback(data)


EHR_TOOLS = [
    find_patient,
    register_patient,
    get_available_slots,
    book_appointment,
    get_patient_appointments,
    cancel_appointment,
]


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info(f"Starting bot")

    elevenlabs_key = os.environ["ELEVENLABS_API_KEY"]
    stt = ElevenLabsRealtimeSTTService(api_key=elevenlabs_key)
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        voice_id="SAz9YHcvj6GT2YYXdXww",
    )

    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"])

    for tool_fn in EHR_TOOLS:
        llm.register_direct_function(tool_fn)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
    ]

    context = LLMContext(messages, tools=ToolsSchema(standard_tools=EHR_TOOLS))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
            ),
        ),
    )

    rtvi = RTVIProcessor()

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,  # RTVI processor
            stt,
            user_aggregator,  # User responses
            llm,  # LLM
            tts,  # TTS
            transport.output(),  # Transport bot output
            assistant_aggregator,  # Assistant spoken responses
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")
        # Kick off the conversation.
        messages.append(
            {
                "role": "system",
                "content": "Say hello and briefly introduce yourself as a digital assistant from the Prosper Health clinic.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point for the bot starter."""

    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        ),
    }

    transport = await create_transport(runner_args, transport_params)

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
