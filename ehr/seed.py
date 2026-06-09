from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

from .models import AppointmentSlot

SLOT_DURATION_MINUTES = 30
BUSINESS_HOURS_START = 9  # 9 AM
BUSINESS_HOURS_END = 17  # 5 PM
SEED_DAYS_AHEAD = 30


def seed_slots(db: Session, today: date | None = None) -> int:
    """Seed business-hours slots for the next SEED_DAYS_AHEAD days if none exist."""
    if today is None:
        today = date.today()

    cutoff = datetime.combine(today, __import__("datetime").time.min)
    existing = db.query(AppointmentSlot).filter(AppointmentSlot.starts_at >= cutoff).count()
    if existing > 0:
        return 0

    slots = []
    for day_offset in range(SEED_DAYS_AHEAD):
        day = today + timedelta(days=day_offset)
        if day.weekday() >= 5:  # skip weekends
            continue
        current = datetime(day.year, day.month, day.day, BUSINESS_HOURS_START, 0)
        end = datetime(day.year, day.month, day.day, BUSINESS_HOURS_END, 0)
        while current < end:
            slots.append(AppointmentSlot(starts_at=current, duration_minutes=SLOT_DURATION_MINUTES))
            current += timedelta(minutes=SLOT_DURATION_MINUTES)

    db.add_all(slots)
    db.commit()
    logger.info(f"Seeded {len(slots)} slots from {today} to {today + timedelta(days=SEED_DAYS_AHEAD)}")
    return len(slots)
