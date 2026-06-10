"""Scenario: book an appointment for a first-time patient."""

import datetime

from loguru import logger

from ehr.database import SessionLocal
from ehr.models import Patient

PATIENT_FIRST = "Maria"
PATIENT_LAST = "Garcia"
PATIENT_DOB = datetime.date(1985, 3, 15)

PERSONA_PROMPT = (
    "You are Maria Garcia, date of birth March 15, 1985. You are calling Prosper Health clinic "
    "to book your very first appointment — you have never been a patient here before. "
    "You want an appointment next week, any morning slot works. "
    "Be conversational and natural, as if speaking on the phone. "
    "Provide your details when asked. Confirm the appointment when offered. "
    "Say goodbye warmly once the booking is confirmed."
)

JUDGE_CRITERIA = [
    "The agent greeted the patient warmly and introduced itself as a Prosper Health assistant.",
    "The agent asked for the patient's full name and date of birth before attempting to look them up.",
    "The agent offered to register Maria as a new patient when she was not found in the system.",
    "The agent presented available appointment slots in a human-readable format (e.g. 'Monday the 9th at 10 AM').",
    "The agent confirmed the booking details with the patient before calling book_appointment.",
    "The agent called end_conversation after the task was complete and the patient said goodbye.",
]


async def setup_fixture() -> None:
    """Remove any pre-existing Maria Garcia / 1985-03-15 data so the scenario starts clean."""
    db = SessionLocal()
    try:
        patient = (
            db.query(Patient)
            .filter(
                Patient.first_name == PATIENT_FIRST,
                Patient.last_name == PATIENT_LAST,
                Patient.date_of_birth == PATIENT_DOB,
            )
            .first()
        )
        if patient:
            for appt in list(patient.appointments):
                db.delete(appt)
            db.delete(patient)
            db.commit()
            logger.info(
                f"Fixture: removed existing patient {PATIENT_FIRST} {PATIENT_LAST} and their appointments"
            )
        else:
            logger.info(f"Fixture: no existing patient {PATIENT_FIRST} {PATIENT_LAST} found — DB is clean")
    finally:
        db.close()


def assert_patient_exists(db) -> None:
    patient = (
        db.query(Patient)
        .filter(Patient.first_name == PATIENT_FIRST, Patient.last_name == PATIENT_LAST)
        .first()
    )
    assert patient is not None, f"Patient {PATIENT_FIRST} {PATIENT_LAST} not found in DB"


def assert_appointment_scheduled(db) -> None:
    patient = (
        db.query(Patient)
        .filter(Patient.first_name == PATIENT_FIRST, Patient.last_name == PATIENT_LAST)
        .first()
    )
    assert patient is not None, f"Patient {PATIENT_FIRST} {PATIENT_LAST} not found in DB"
    scheduled = [a for a in patient.appointments if a.status == "scheduled"]
    assert len(scheduled) >= 1, (
        f"Expected ≥1 scheduled appointment for {PATIENT_FIRST} {PATIENT_LAST}, found {len(scheduled)}"
    )


SCENARIO = {
    "name": "book_new_patient",
    "persona_prompt": PERSONA_PROMPT,
    "setup_fixture": setup_fixture,
    "judge_criteria": JUDGE_CRITERIA,
    "db_assertions": [assert_patient_exists, assert_appointment_scheduled],
}
