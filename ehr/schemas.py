from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    phone: str | None = None
    email: str | None = None


class PatientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    date_of_birth: date
    phone: str | None
    email: str | None
    created_at: datetime


class SlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    starts_at: datetime
    duration_minutes: int


class AppointmentCreate(BaseModel):
    patient_id: int
    slot_id: int
    notes: str | None = None


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    slot_id: int
    status: str
    notes: str | None
    created_at: datetime
    slot: SlotResponse
