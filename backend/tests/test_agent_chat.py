import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")
os.environ["SEED_PATIENT_COUNT"] = "100"

from sqlalchemy import select

from app.agent import CAPABILITIES, FALLBACKS, chat
from app.db import Base, SessionLocal, engine, initialise_database
from app.main import execute_scan
from app.models import Patient, User
from app.seed import seed


def setup_module():
    Base.metadata.drop_all(engine)
    initialise_database()
    seed()
    with SessionLocal() as db:
        execute_scan(db, trigger="test", actor=db.scalar(select(User).where(User.email == "admin@sentinel.demo")))
        db.commit()


def _users(db):
    admin = db.scalar(select(User).where(User.role == "administrator"))
    clinician = db.scalar(select(User).where(User.role == "clinician"))
    researcher = db.scalar(select(User).where(User.role == "researcher"))
    return admin, clinician, researcher


def test_unrecognised_messages_do_not_repeat_the_same_reply():
    with SessionLocal() as db:
        admin, _, _ = _users(db)
        first = chat(db, admin, "what is the weather")
        second = chat(db, admin, "what is the weather", first["conversation_id"])
        third = chat(db, admin, "tell me a joke", second["conversation_id"])
        assert all(reply["message"] in FALLBACKS for reply in (first, second, third))
        assert first["message"] != second["message"] and second["message"] != third["message"]


def test_intents_give_distinct_answers():
    with SessionLocal() as db:
        admin, _, _ = _users(db)
        replies = {chat(db, admin, text)["message"] for text in ("hi", "help", "status", "thanks", "SYN-DM-013")}
        assert len(replies) == 5
        assert CAPABILITIES in replies


def test_status_and_patient_summary_respect_roles_and_scope():
    with SessionLocal() as db:
        admin, clinician, researcher = _users(db)
        assert "patients are monitored" in chat(db, admin, "how many alerts are open today")["message"]
        assert "Researchers" in chat(db, researcher, "status")["message"]
        assert "administrators and clinicians only" in chat(db, researcher, "SYN-DM-013")["message"]
        other = db.scalar(select(Patient).where(Patient.organization_id != clinician.organization_id))
        assert "cannot find" in chat(db, clinician, f"show {other.reference}")["message"]
        own = db.scalar(select(Patient).where(Patient.organization_id == clinician.organization_id))
        summary = chat(db, clinician, own.reference)["message"]
        assert summary.startswith(own.reference) and "HbA1c" in summary and "not a diagnosis" in summary
