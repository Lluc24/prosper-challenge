from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException
from loguru import logger
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .models import Appointment, AppointmentSlot, Patient
from .schemas import (
    AppointmentCreate,
    AppointmentResponse,
    PatientCreate,
    PatientResponse,
    SlotResponse,
)
from .seed import seed_slots


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        count = seed_slots(db)
        if count:
            logger.info(f"Seeded {count} appointment slots")
    finally:
        db.close()
    yield


app = FastAPI(title="Prosper Health EHR", lifespan=lifespan)


# --- Patients ---


@app.post("/patients", response_model=PatientResponse, status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(Patient)
        .filter(
            Patient.first_name == payload.first_name,
            Patient.last_name == payload.last_name,
            Patient.date_of_birth == payload.date_of_birth,
        )
        .first()
    )
    if existing:
        logger.warning(
            f"Patient already exists: {payload.first_name} {payload.last_name} ({payload.date_of_birth})"
        )
        raise HTTPException(status_code=409, detail="Patient already exists")
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    logger.info(
        f"Patient registered: id={patient.id} name='{patient.first_name} {patient.last_name}' dob={patient.date_of_birth}"
    )
    return patient


@app.get("/patients", response_model=PatientResponse)
def find_patient(
    first_name: str,
    last_name: str,
    date_of_birth: date,
    db: Session = Depends(get_db),
):
    patient = (
        db.query(Patient)
        .filter(
            Patient.first_name == first_name,
            Patient.last_name == last_name,
            Patient.date_of_birth == date_of_birth,
        )
        .first()
    )
    if not patient:
        logger.warning(f"Patient not found: '{first_name} {last_name}' dob={date_of_birth}")
        raise HTTPException(status_code=404, detail="Patient not found")
    logger.info(f"Patient found: id={patient.id} name='{patient.first_name} {patient.last_name}'")
    return patient


# --- Slots ---


@app.get("/slots", response_model=list[SlotResponse])
def list_slots(start_date: date, end_date: date | None = None, db: Session = Depends(get_db)):
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date or start_date, datetime.max.time())

    booked_slot_ids = (
        db.query(Appointment.slot_id).filter(Appointment.status == "scheduled").scalar_subquery()
    )

    slots = (
        db.query(AppointmentSlot)
        .filter(
            AppointmentSlot.starts_at >= start_dt,
            AppointmentSlot.starts_at <= end_dt,
            AppointmentSlot.id.not_in(booked_slot_ids),
        )
        .order_by(AppointmentSlot.starts_at)
        .all()
    )
    logger.info(f"Slots queried: {start_date} to {end_date or start_date} — {len(slots)} available")
    return slots


# --- Appointments ---


@app.post("/appointments", response_model=AppointmentResponse, status_code=201)
def create_appointment(payload: AppointmentCreate, db: Session = Depends(get_db)):
    patient = db.get(Patient, payload.patient_id)
    if not patient:
        logger.warning(f"Appointment creation failed: patient id={payload.patient_id} not found")
        raise HTTPException(status_code=404, detail="Patient not found")

    slot = db.get(AppointmentSlot, payload.slot_id)
    if not slot:
        logger.warning(f"Appointment creation failed: slot id={payload.slot_id} not found")
        raise HTTPException(status_code=404, detail="Slot not found")

    conflict = (
        db.query(Appointment)
        .filter(Appointment.slot_id == payload.slot_id, Appointment.status == "scheduled")
        .first()
    )
    if conflict:
        logger.warning(f"Appointment creation failed: slot id={payload.slot_id} already booked")
        raise HTTPException(status_code=409, detail="Slot already booked")

    appointment = Appointment(**payload.model_dump(), status="scheduled")
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    logger.info(
        f"Appointment scheduled: id={appointment.id} patient_id={appointment.patient_id} "
        f"slot={slot.starts_at}"
    )
    return appointment


@app.get("/patients/{patient_id}/appointments", response_model=list[AppointmentResponse])
def get_patient_appointments(patient_id: int, db: Session = Depends(get_db)):
    patient = db.get(Patient, patient_id)
    if not patient:
        logger.warning(f"Appointments lookup failed: patient id={patient_id} not found")
        raise HTTPException(status_code=404, detail="Patient not found")
    appointments = (
        db.query(Appointment)
        .filter(Appointment.patient_id == patient_id, Appointment.status == "scheduled")
        .order_by(Appointment.created_at)
        .all()
    )
    logger.info(
        f"Appointments queried: patient_id={patient_id} — {len(appointments)} scheduled"
    )
    return appointments


@app.delete("/appointments/{appointment_id}", response_model=AppointmentResponse)
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db)):
    appointment = db.get(Appointment, appointment_id)
    if not appointment:
        logger.warning(f"Cancellation failed: appointment id={appointment_id} not found")
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appointment.status == "cancelled":
        logger.warning(f"Cancellation failed: appointment id={appointment_id} already cancelled")
        raise HTTPException(status_code=409, detail="Appointment already cancelled")
    appointment.status = "cancelled"
    db.commit()
    db.refresh(appointment)
    logger.info(f"Appointment cancelled: id={appointment.id} slot={appointment.slot.starts_at}")
    return appointment
