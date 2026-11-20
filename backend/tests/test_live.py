import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_sentinel.db")
os.environ.setdefault("APP_SECRET", "test-secret")

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.chain import verify_batch
from app.db import Base, SessionLocal, engine, initialise_database
from app.live import (
    SIM_SOURCE_PREFIX, StartPayload, active_run, build_dashboard, reset_simulation, simulation_tick, start_run, stop_run,
)
from app.models import Alert, LiveStatusSnapshot, Observation, Patient, Scan, SourceBatch, User
from app.security import require_roles
from app.seed import seed


def setup_module():
    Base.metadata.drop_all(engine)
    initialise_database()
    seed()


def _user(db, email):
    return db.scalar(select(User).where(User.email == email))


def test_live_monitor_is_staff_only():
    guard = require_roles("administrator", "clinician")
    with SessionLocal() as db:
        for email in ("researcher.approved@sentinel.demo", "caio@sentinel.demo"):
            with pytest.raises(HTTPException):
                guard(_user(db, email))
        assert guard(_user(db, "clinician.aster@sentinel.demo"))


def test_dashboard_without_run_uses_live_assessment():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        data = build_dashboard(db, admin)
        assert data["current"]["total"] == 100
        assert data["series"] == []
        assert data["simulation"]["can_control"] is True
        assert data["data_label"] == "Synthetic data — demonstration only"


def test_simulation_writes_through_normal_scan_and_scopes_clinicians():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        before = db.scalar(select(func.count()).select_from(Observation))
        run = start_run(db, admin, StartPayload(tick_seconds=2, simulated_days_per_tick=30))
        db.commit()
        with pytest.raises(HTTPException):
            start_run(db, admin, StartPayload())
        for _ in range(3):
            simulation_tick(db, run)
        db.commit()

        written = db.scalar(select(func.count()).select_from(Observation).where(Observation.source_id.like(f"{SIM_SOURCE_PREFIX}%")))
        assert written > 0 and written == run.observations_written
        assert db.scalar(select(func.count()).select_from(Observation)) == before + written
        sim_obs = db.scalars(select(Observation).where(Observation.source_id.like(f"{SIM_SOURCE_PREFIX}%"))).all()
        assert all(item.terminology_status == "verified" and item.provenance["simulated_time"] for item in sim_obs)
        assert db.scalar(select(func.count()).select_from(Scan).where(Scan.trigger == "live_simulation")) == 3
        assert db.scalar(select(func.count()).select_from(LiveStatusSnapshot)) == 4  # baseline + 3 ticks

        admin_view = build_dashboard(db, admin)
        assert len(admin_view["series"]) == 4
        assert admin_view["current"]["total"] == 100
        assert admin_view["simulation"]["ticks"] == 3

        clinician = _user(db, "clinician.aster@sentinel.demo")
        clinician_view = build_dashboard(db, clinician)
        assert clinician_view["current"]["total"] == 34
        assert all(point["by_status"] and sum(point["by_status"].values()) == 34 for point in clinician_view["series"])
        assert clinician_view["simulation"]["can_control"] is False
        org_patients = {p.reference for p in db.scalars(select(Patient).where(Patient.organization_id == clinician.organization_id))}
        assert all(alert["patient"]["reference"] in org_patients for alert in clinician_view["recent_alerts"])

        stop_run(db, run, "test", admin)
        db.commit()
        assert active_run(db) is None


def test_reset_restores_source_integrity():
    with SessionLocal() as db:
        admin = _user(db, "admin@sentinel.demo")
        batch = db.scalar(select(SourceBatch).order_by(SourceBatch.created_at))
        assert verify_batch(batch, list(db.scalars(select(Observation)).all()))["local_match"] is False
        removed = reset_simulation(db, admin)
        db.commit()
        assert removed["observations"] > 0
        assert db.scalar(select(func.count()).select_from(Observation).where(Observation.source_id.like(f"{SIM_SOURCE_PREFIX}%"))) == 0
        assert db.scalar(select(func.count()).select_from(Scan).where(Scan.trigger == "live_simulation")) == 0
        assert db.scalar(select(func.count()).select_from(LiveStatusSnapshot)) == 0
        assert verify_batch(batch, list(db.scalars(select(Observation)).all()))["local_match"] is True
        assert build_dashboard(db, admin)["series"] == []
        assert db.scalar(select(func.count()).select_from(Alert)) >= 0


def test_seed_cohort_size_scales_care_mix():
    from app.seed import BASE_CARE_MATRIX, care_matrix_for

    assert care_matrix_for(100) == BASE_CARE_MATRIX
    for total in (12, 500, 777, 5000):
        matrix = care_matrix_for(total)
        assert sum(count for *_, count in matrix) == total
        assert [(org, setting) for org, setting, _ in matrix] == [(org, setting) for org, setting, _ in BASE_CARE_MATRIX]
