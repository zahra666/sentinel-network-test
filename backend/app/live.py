"""Staff-only live monitor: accelerated-time synthetic data and aggregate review-state trends.

Synthetic data — demonstration only. The simulator writes fictional HbA1c/FPG
observations, then runs the normal Sync -> Analyse -> Route scan so every
displayed state comes from the existing deterministic review tree. It never
diagnoses, prescribes, classifies urgency or contacts anyone.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db
from .models import (
    Alert, AuditEvent, Finding, LiveStatusSnapshot, NotificationAttempt, Observation, Organization,
    Patient, Scan, SimulationRun, User,
)
from .security import require_roles
from .services.analyse import analyse_patients
from .terminology import LOINC_SYSTEM, LOINC_VERSION, TerminologyError, map_measurement

logger = logging.getLogger("sentinel.live")
router = APIRouter(prefix="/api/live", tags=["live-monitor"])

SIM_SOURCE_PREFIX = "SIM-"
SIM_TRIGGER = "live_simulation"
STATUSES = [
    {"key": "no_qualifying_evidence", "label": "No qualifying evidence"},
    {"key": "confirmation_gap", "label": "Confirmation gap"},
    {"key": "evidence_requires_clinician_review", "label": "Clinician review required"},
    {"key": "data_quality_review", "label": "Data-quality review"},
    {"key": "out_of_scope", "label": "Out of scope (pregnancy)"},
]
STATUS_KEYS = [item["key"] for item in STATUSES]
FALLBACK_SIM_START = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)

# One lock serialises the background runner with start/stop/step/reset requests.
_lock = threading.Lock()
_runner_started = False


class StartPayload(BaseModel):
    tick_seconds: int | None = Field(default=None, ge=2, le=60)
    simulated_days_per_tick: int | None = Field(default=None, ge=1, le=30)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps; everything here is stored as UTC."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- simulation


def active_run(db: Session) -> SimulationRun | None:
    return db.scalar(select(SimulationRun).where(SimulationRun.status == "running").order_by(SimulationRun.started_at.desc()))


def latest_run(db: Session) -> SimulationRun | None:
    return db.scalar(select(SimulationRun).where(SimulationRun.status != "reset").order_by(SimulationRun.started_at.desc()))


def _profile(reference: str) -> dict:
    """Stable per-patient synthetic profile so trends look longitudinal, not white noise.

    Each fictional T2DM profile gets its own glycaemic baseline (spread around the
    illustrative rule thresholds), a long-run trajectory and a personal cycle phase.
    """
    digest = int(hashlib.sha256(reference.encode()).hexdigest(), 16)
    bucket = (digest // 10**6) % 20
    trajectory = "rising" if bucket < 5 else "falling" if bucket < 9 else "stable"
    return {
        "trajectory": trajectory,
        "hba1c_base": 5.4 + 1.5 * ((digest % 1000) / 1000),       # 5.4-6.9 %
        "fpg_base": 4.8 + 2.8 * (((digest // 1000) % 1000) / 1000),  # 4.8-7.6 mmol/L
        "phase": ((digest // 10**8) % 360) * math.pi / 180,
    }


SLOPES = {"hba1c": {"rising": 0.12, "falling": -0.10, "stable": 0.0}, "fpg": {"rising": 0.18, "falling": -0.15, "stable": 0.0}}
LIMITS = {"hba1c": (5.0, 10.5, 0.22), "fpg": (4.3, 13.0, 0.45)}  # low, high, measurement noise


def _target(profile: dict, kind: str, elapsed_days: float) -> float:
    low, high, _ = LIMITS[kind]
    base = profile[f"{kind}_base"] + SLOPES[kind][profile["trajectory"]] * elapsed_days / 30
    return min(high - 0.5, max(low + 0.4, base))


def _latest_values(db: Session) -> dict[str, dict[str, float]]:
    latest: dict[str, dict[str, tuple[datetime, float]]] = defaultdict(dict)
    rows = db.execute(
        select(Observation.patient_id, Observation.clinical_key, Observation.observed_at, Observation.normalised_value)
        .where(Observation.clinical_key.in_(("hba1c_ngsp", "fpg")), Observation.normalised_value.is_not(None))
    ).all()
    for patient_id, key, observed_at, value in rows:
        observed_at = as_utc(observed_at)
        current = latest[patient_id].get(key)
        if current is None or observed_at >= current[0]:
            latest[patient_id][key] = (observed_at, value)
    return {pid: {key: pair[1] for key, pair in values.items()} for pid, values in latest.items()}


def _next_value(last: float, target: float, kind: str, wave: float, rng: random.Random) -> float:
    low, high, noise = LIMITS[kind]
    value = last + 0.5 * (target - last) + wave + rng.gauss(0, noise)
    return round(min(high, max(low, value)), 1)


def _observation(patient: Patient, run: SimulationRun, code: str, value: float, unit: str, observed_at: datetime) -> Observation:
    loinc, local = {"hba1c": ("4548-4", "HBA1C"), "fpg": ("14749-6", "FPG")}[code]
    try:
        mapping = map_measurement(system=LOINC_SYSTEM, code=loinc, display=None, version=LOINC_VERSION, original_code=local, value=value, ucum_unit=unit)
    except TerminologyError:  # pragma: no cover - the simulator only emits catalogue units
        raise
    return Observation(
        patient_id=patient.id, code=code, value=value, unit=unit, observed_at=observed_at, ingested_at=now_utc(),
        source_id=f"{SIM_SOURCE_PREFIX}{run.id[:8]}-{run.ticks:05d}-{patient.reference}-{code}", version=1,
        ucum_unit=unit, result_status="final",
        provenance={"source": "live monitor accelerated-time simulation", "synthetic_only": True, "simulated_time": True, "run_id": run.id, **mapping["conversion_provenance"]},
        **{key: mapping[key] for key in ("original_code", "coding_system", "standard_code", "coding_display", "terminology_version", "clinical_key", "terminology_status", "normalised_value", "normalised_ucum_unit")},
    )


def simulation_tick(db: Session, run: SimulationRun) -> dict:
    """Advance simulated time, write synthetic results, run the normal scan, capture a snapshot."""
    from .main import execute_scan  # local import: main imports this router

    settings = get_settings()
    run.ticks += 1
    days = run.simulated_days_per_tick
    previous = as_utc(run.simulated_now)
    run.simulated_now = previous + timedelta(days=days)
    rng = random.Random(f"{run.id}:{run.ticks}")
    elapsed_days = (as_utc(run.simulated_now) - as_utc(run.simulated_start)).days
    cohort_wave = 0.25 * math.sin(2 * math.pi * elapsed_days / 150)  # shared seasonal swing
    probability = min(1.0, days / max(1, settings.live_simulation_mean_test_interval_days))

    latest = _latest_values(db)
    written = 0
    for patient in db.scalars(select(Patient).order_by(Patient.reference)).all():
        if rng.random() >= probability:
            continue
        profile = _profile(patient.reference)
        values = latest.get(patient.id, {})
        observed_at = previous + timedelta(days=rng.randint(1, days), hours=rng.randint(7, 16))
        wave = cohort_wave + 0.15 * math.sin(2 * math.pi * elapsed_days / 90 + profile["phase"])
        hba1c = _next_value(values.get("hba1c_ngsp", profile["hba1c_base"]), _target(profile, "hba1c", elapsed_days), "hba1c", wave, rng)
        fpg = _next_value(values.get("fpg", profile["fpg_base"]), _target(profile, "fpg", elapsed_days), "fpg", wave * 1.6, rng)
        db.add(_observation(patient, run, "hba1c", hba1c, "%", observed_at))
        db.add(_observation(patient, run, "fpg", fpg, "mmol/L", observed_at))
        written += 2
    db.flush()
    run.observations_written += written
    run.last_tick_at = now_utc()
    scan = execute_scan(db, trigger=SIM_TRIGGER)
    snapshot = capture_snapshot(db, run=run, scan=scan)
    return {"tick": run.ticks, "observations_written": written, "new_alerts": scan.finding_count, "snapshot_id": snapshot.id, "simulated_now": as_utc(run.simulated_now).isoformat()}


def capture_snapshot(db: Session, run: SimulationRun | None, scan: Scan | None) -> LiveStatusSnapshot:
    analyses, _ = analyse_patients(db)
    counter: Counter = Counter()
    for patient, result in analyses:
        counter[(patient.organization_id, patient.care_setting, result["status"])] += 1
    open_alerts = dict(db.execute(
        select(Patient.organization_id, func.count(Alert.id)).join(Patient, Patient.id == Alert.patient_id)
        .where(Alert.status == "new").group_by(Patient.organization_id)
    ).all())
    snapshot = LiveStatusSnapshot(
        run_id=run.id if run else None, scan_id=scan.id if scan else None,
        simulated_at=as_utc(run.simulated_now) if run else None,
        counts=[{"organization_id": org, "care_setting": setting, "status": status, "patients": n} for (org, setting, status), n in sorted(counter.items())],
        open_alerts=open_alerts,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def start_run(db: Session, user: User, payload: StartPayload) -> SimulationRun:
    settings = get_settings()
    if active_run(db):
        raise HTTPException(status_code=409, detail="A live simulation is already running")
    newest = db.scalar(select(func.max(Observation.observed_at)))
    start = max(as_utc(newest) or FALLBACK_SIM_START, FALLBACK_SIM_START)
    run = SimulationRun(
        started_by=user.id, status="running",
        expires_at=now_utc() + timedelta(minutes=settings.live_simulation_max_minutes),
        tick_seconds=payload.tick_seconds or settings.live_simulation_tick_seconds,
        simulated_days_per_tick=payload.simulated_days_per_tick or settings.live_simulation_days_per_tick,
        simulated_start=start, simulated_now=start,
    )
    db.add(run)
    db.flush()
    capture_snapshot(db, run=run, scan=None)
    db.add(AuditEvent(actor_id=user.id, event_type="live.simulation_started", entity_type="simulation_run", entity_id=run.id,
                      detail={"synthetic_only": True, "tick_seconds": run.tick_seconds, "simulated_days_per_tick": run.simulated_days_per_tick}))
    return run


def stop_run(db: Session, run: SimulationRun, reason: str, actor: User | None = None) -> None:
    run.status = "stopped" if reason != "error" else "error"
    run.stopped_at = now_utc()
    db.add(AuditEvent(actor_id=actor.id if actor else None, event_type="live.simulation_stopped", entity_type="simulation_run", entity_id=run.id,
                      detail={"reason": reason, "ticks": run.ticks, "observations_written": run.observations_written}))


def reset_simulation(db: Session, user: User) -> dict:
    """Remove simulated observations and the scans/findings/alerts they produced; audit history is kept."""
    if run := active_run(db):
        stop_run(db, run, "reset", user)
    scan_ids = list(db.scalars(select(Scan.id).where(Scan.trigger == SIM_TRIGGER)).all())
    finding_ids = list(db.scalars(select(Finding.id).where(Finding.scan_id.in_(scan_ids))).all()) if scan_ids else []
    removed = {"findings": len(finding_ids), "scans": len(scan_ids)}
    if finding_ids:
        db.execute(delete(NotificationAttempt).where(NotificationAttempt.finding_id.in_(finding_ids)))
        db.execute(delete(Alert).where(Alert.finding_id.in_(finding_ids)))
        db.execute(delete(Finding).where(Finding.id.in_(finding_ids)))
    db.execute(delete(LiveStatusSnapshot))
    if scan_ids:
        db.execute(delete(Scan).where(Scan.id.in_(scan_ids)))
    removed["observations"] = db.execute(delete(Observation).where(Observation.source_id.like(f"{SIM_SOURCE_PREFIX}%"))).rowcount
    for run in db.scalars(select(SimulationRun).where(SimulationRun.status != "reset")).all():
        run.status = "reset"
    db.add(AuditEvent(actor_id=user.id, event_type="live.simulation_reset", entity_type="simulation_run", entity_id="all", detail={"synthetic_only": True, **removed}))
    return removed


def _runner_loop() -> None:
    while True:
        time.sleep(1)
        try:
            with _lock, SessionLocal() as db:
                run = active_run(db)
                if not run:
                    continue
                now = now_utc()
                if now >= as_utc(run.expires_at):
                    stop_run(db, run, "expired")
                    db.commit()
                    continue
                last = as_utc(run.last_tick_at or run.started_at)
                if (now - last).total_seconds() >= run.tick_seconds:
                    simulation_tick(db, run)
                    db.commit()
        except Exception as exc:  # keep the API alive; record the failure honestly
            logger.exception("Live simulation tick failed")
            try:
                with _lock, SessionLocal() as db:
                    if run := active_run(db):
                        run.error_summary = str(exc)[:500]
                        stop_run(db, run, "error")
                        db.commit()
            except Exception:  # pragma: no cover
                logger.exception("Could not record live simulation failure")


def start_background_runner() -> None:
    global _runner_started
    if _runner_started or not get_settings().live_simulation_enabled:
        return
    _runner_started = True
    threading.Thread(target=_runner_loop, name="sentinel-live-simulation", daemon=True).start()


# --------------------------------------------------------------------------- read model


def _blank() -> dict[str, int]:
    return {key: 0 for key in STATUS_KEYS}


def _scoped(counts: list[dict], organization_id: str | None) -> list[dict]:
    return [row for row in counts if organization_id is None or row["organization_id"] == organization_id]


def _by_status(rows: list[dict]) -> dict[str, int]:
    totals = _blank()
    for row in rows:
        totals[row["status"]] = totals.get(row["status"], 0) + row["patients"]
    return totals


def _live_counts(db: Session) -> list[dict]:
    analyses, _ = analyse_patients(db)
    counter: Counter = Counter((p.organization_id, p.care_setting, r["status"]) for p, r in analyses)
    return [{"organization_id": org, "care_setting": setting, "status": status, "patients": n} for (org, setting, status), n in counter.items()]


def serialize_run(run: SimulationRun | None) -> dict | None:
    if not run:
        return None
    return {
        "id": run.id, "status": run.status, "ticks": run.ticks, "tick_seconds": run.tick_seconds,
        "simulated_days_per_tick": run.simulated_days_per_tick, "observations_written": run.observations_written,
        "simulated_start": as_utc(run.simulated_start).isoformat(), "simulated_now": as_utc(run.simulated_now).isoformat(),
        "started_at": as_utc(run.started_at).isoformat(), "expires_at": as_utc(run.expires_at).isoformat(),
        "stopped_at": as_utc(run.stopped_at).isoformat() if run.stopped_at else None, "error_summary": run.error_summary,
    }


def build_dashboard(db: Session, user: User, points: int = 72) -> dict:
    from .main import serialize_alert

    org_id = user.organization_id if user.role == "clinician" else None
    organization = db.get(Organization, org_id) if org_id else None
    run = latest_run(db)
    snapshots: list[LiveStatusSnapshot] = []
    if run:
        snapshots = list(reversed(db.scalars(
            select(LiveStatusSnapshot).where(LiveStatusSnapshot.run_id == run.id)
            .order_by(LiveStatusSnapshot.captured_at.desc()).limit(points)
        ).all()))
    current_rows = _scoped(snapshots[-1].counts if snapshots else _live_counts(db), org_id)

    by_setting: dict[str, dict[str, int]] = defaultdict(_blank)
    for row in current_rows:
        by_setting[row["care_setting"]][row["status"]] += row["patients"]

    alert_query = select(Alert).join(Finding, Finding.id == Alert.finding_id)
    open_query = select(func.count(Alert.id)).where(Alert.status == "new")
    if org_id:
        alert_query = alert_query.join(Patient, Patient.id == Alert.patient_id).where(Patient.organization_id == org_id)
        open_query = open_query.join(Patient, Patient.id == Alert.patient_id).where(Patient.organization_id == org_id)
    recent: list[Alert] = []
    seen: set[str] = set()
    for alert in db.scalars(alert_query.order_by(Finding.created_at.desc()).limit(60)).all():
        if alert.patient_id not in seen:  # newest alert per patient keeps the list readable
            seen.add(alert.patient_id)
            recent.append(alert)
        if len(recent) == 6:
            break

    series = []
    for snap in snapshots:
        alerts = snap.open_alerts or {}
        series.append({
            "captured_at": as_utc(snap.captured_at).isoformat(),
            "simulated_at": as_utc(snap.simulated_at).isoformat() if snap.simulated_at else None,
            "by_status": _by_status(_scoped(snap.counts, org_id)),
            "open_alerts": alerts.get(org_id, 0) if org_id else sum(alerts.values()),
        })

    current = _by_status(current_rows)
    return {
        "data_label": "Synthetic data — demonstration only",
        "time_label": "Simulated time (accelerated)" if run else "No simulation run",
        "scope": {"organization": organization.name if organization else "All hospitals", "role": user.role},
        "statuses": STATUSES,
        "simulation": {**(serialize_run(run) or {}), "available": get_settings().live_simulation_enabled, "can_control": user.role == "administrator"},
        "current": {"total": sum(current.values()), "by_status": current, "open_alerts": db.scalar(open_query) or 0},
        "series": series,
        "by_setting": [{"care_setting": name, "total": sum(values.values()), "by_status": values} for name, values in sorted(by_setting.items())],
        "recent_alerts": [serialize_alert(alert, db) for alert in recent],
        "integrity_note": "Simulated observations are outside the anchored source batch, so ledger verification reports a mismatch until the simulation is reset.",
    }


# --------------------------------------------------------------------------- routes


@router.get("/dashboard")
def live_dashboard(points: int = Query(default=72, ge=2, le=500), user: User = Depends(require_roles("administrator", "clinician")), db: Session = Depends(get_db)) -> dict:
    return build_dashboard(db, user, points)


@router.post("/simulation/start")
def start_simulation(payload: StartPayload | None = None, user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    if not get_settings().live_simulation_enabled:
        raise HTTPException(status_code=409, detail="Live simulation is disabled (LIVE_SIMULATION_ENABLED=false)")
    with _lock:
        run = start_run(db, user, payload or StartPayload())
        db.commit()
    return build_dashboard(db, user)


@router.post("/simulation/stop")
def stop_simulation(user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    with _lock:
        run = active_run(db)
        if not run:
            raise HTTPException(status_code=409, detail="No live simulation is running")
        stop_run(db, run, "stopped_by_administrator", user)
        db.commit()
    return build_dashboard(db, user)


@router.post("/simulation/step")
def step_simulation(user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    with _lock:
        run = active_run(db)
        if not run:
            raise HTTPException(status_code=409, detail="Start a live simulation before stepping it")
        simulation_tick(db, run)
        db.commit()
    return build_dashboard(db, user)


@router.post("/simulation/reset")
def reset(user: User = Depends(require_roles("administrator")), db: Session = Depends(get_db)) -> dict:
    with _lock:
        removed = reset_simulation(db, user)
        db.commit()
    return {"removed": removed, "dashboard": build_dashboard(db, user)}
