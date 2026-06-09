from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")

    __table_args__ = (
        UniqueConstraint("first_name", "last_name", "date_of_birth", name="uq_patient_identity"),
    )


class AppointmentSlot(Base):
    __tablename__ = "appointment_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, unique=True, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)

    appointment: Mapped["Appointment | None"] = relationship(back_populates="slot", uselist=False)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    slot_id: Mapped[int] = mapped_column(
        ForeignKey("appointment_slots.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String, default="scheduled")
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    slot: Mapped[AppointmentSlot] = relationship(back_populates="appointment")
