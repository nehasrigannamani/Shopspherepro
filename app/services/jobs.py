"""Persistent background jobs with status tracking and bounded retries."""
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.background_job import BackgroundJob, ReportSchedule
from app.services import exports
from app.services.delivery import dispatch_pending
from app.services.reports import ReportFilters, build_report

logger = logging.getLogger("shopsphere.jobs")

FREQUENCY_DELTAS = {
    "DAILY": timedelta(days=1),
    "WEEKLY": timedelta(weeks=1),
    "MONTHLY": timedelta(days=30),
}
RETRY_BACKOFF_MINUTES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_job(
    db: Session,
    job_type: str,
    params: dict | None,
    created_by: int | None,
    schedule_id: int | None = None,
) -> BackgroundJob:
    job = BackgroundJob(
        job_type=job_type,
        status="PENDING",
        params=params or {},
        max_attempts=settings.job_max_attempts,
        created_by=created_by,
        schedule_id=schedule_id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _execute(db: Session, job: BackgroundJob) -> tuple[str | None, str]:
    params = job.params or {}

    if job.job_type == "NOTIFICATION_DISPATCH":
        stats = dispatch_pending(db, limit=int(params.get("limit", 100)))
        return None, f"Notification dispatch: {stats}"

    if job.job_type in {"REPORT_EXPORT", "SCHEDULED_SUMMARY"}:
        fmt = params.get("format", "csv")
        name = params["report"]
        result = build_report(db, name, ReportFilters.from_dict(params.get("filters")))
        content = exports.render(result, fmt)
        os.makedirs(settings.export_dir, exist_ok=True)
        path = os.path.join(settings.export_dir, f"job-{job.id}-{name}.{fmt}")
        with open(path, "wb") as handle:
            handle.write(content)
        return path, f"{name} report: {len(result['rows'])} rows, {len(content)} bytes"

    raise ValueError(f"Unknown job type {job.job_type}")


def run_job(job_id: int) -> None:
    """Run one job in its own DB session. Safe to call from BackgroundTasks."""
    db = SessionLocal()
    try:
        job = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.id == job_id)
            .with_for_update()
            .first()
        )
        if job is None or job.status not in {"PENDING", "FAILED"}:
            return
        if job.attempts >= job.max_attempts:
            return

        job.status = "RUNNING"
        job.attempts += 1
        job.started_at = _now()
        job.error_message = None
        db.commit()

        try:
            path, summary = _execute(db, job)
            job.status = "SUCCEEDED"
            job.result_path = path
            job.result_summary = summary
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            job = db.get(BackgroundJob, job_id)
            job.status = "FAILED"
            job.error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
            logger.exception("Background job %s failed", job_id)
        job.finished_at = _now()
        db.commit()
    finally:
        db.close()


def enqueue_due_schedules(db: Session) -> list[int]:
    now = _now()
    due = (
        db.query(ReportSchedule)
        .filter(ReportSchedule.is_active.is_(True), ReportSchedule.next_run_at <= now)
        .with_for_update()
        .all()
    )
    job_ids = []
    for schedule in due:
        job = BackgroundJob(
            job_type="SCHEDULED_SUMMARY",
            status="PENDING",
            params={
                "report": schedule.report_name,
                "format": schedule.export_format,
                "filters": schedule.filters or {},
            },
            max_attempts=settings.job_max_attempts,
            created_by=schedule.created_by,
            schedule_id=schedule.id,
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)
        schedule.last_run_at = now
        schedule.next_run_at = now + FREQUENCY_DELTAS[schedule.frequency]
    db.commit()
    return job_ids


def retryable_job_ids(db: Session) -> list[int]:
    cutoff = _now() - timedelta(minutes=RETRY_BACKOFF_MINUTES)
    rows = (
        db.query(BackgroundJob.id)
        .filter(
            BackgroundJob.status == "FAILED",
            BackgroundJob.attempts < BackgroundJob.max_attempts,
            BackgroundJob.finished_at <= cutoff,
        )
        .all()
    )
    return [r[0] for r in rows]


def scheduler_tick() -> dict:
    """One scheduler pass: enqueue due schedules, dispatch notifications,
    run pending jobs and retry eligible failures."""
    db = SessionLocal()
    try:
        scheduled = enqueue_due_schedules(db)
        pending = [
            r[0]
            for r in db.query(BackgroundJob.id)
            .filter(BackgroundJob.status == "PENDING")
            .all()
        ]
        retries = retryable_job_ids(db)
    finally:
        db.close()

    for job_id in dict.fromkeys(pending + retries):
        run_job(job_id)

    # Notification deliveries are processed on every pass.
    db = SessionLocal()
    try:
        dispatch = dispatch_pending(db)
    finally:
        db.close()

    return {
        "scheduled_jobs": scheduled,
        "jobs_run": len(set(pending + retries)),
        "retried": retries,
        "notification_dispatch": dispatch,
    }
