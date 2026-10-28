import os
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.config import settings
from app.db.session import get_db
from app.models.background_job import BackgroundJob, ReportSchedule
from app.models.user import User, UserRole
from app.services import exports
from app.services.jobs import (
    FREQUENCY_DELTAS,
    _now,
    create_job,
    run_job,
    scheduler_tick,
)
from app.services.reports import (
    BUILDERS,
    REPORT_ACCESS,
    ReportFilters,
    build_report,
)

router = APIRouter(
    prefix="/report-exports",
    tags=["Report Export and Background Tasks"],
)

EXPORT_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.FINANCE_MANAGER,
    UserRole.INVENTORY_MANAGER,
    UserRole.ORDER_MANAGER,
    UserRole.PRODUCT_MANAGER,
)
ADMIN_ROLES = (UserRole.SUPER_ADMIN, UserRole.ADMIN)


def _check_report(name: str, user: User) -> None:
    if name not in BUILDERS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown report. Available: {', '.join(sorted(BUILDERS))}",
        )
    if user.role.value not in REPORT_ACCESS[name]:
        raise HTTPException(status_code=403, detail="You may not access this report")


def _filters(
    date_from: date | None,
    date_to: date | None,
    status: str | None,
    category_id: int | None,
    warehouse_id: int | None,
) -> ReportFilters:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to.")
    return ReportFilters(date_from, date_to, status, category_id, warehouse_id)


class ExportJobRequest(BaseModel):
    report: str
    format: str = Field(default="csv", pattern=r"^(csv|xlsx|pdf)$")
    date_from: date | None = None
    date_to: date | None = None
    status: str | None = None
    category_id: int | None = Field(default=None, gt=0)
    warehouse_id: int | None = Field(default=None, gt=0)


class ScheduleRequest(ExportJobRequest):
    frequency: str = Field(pattern=r"^(DAILY|WEEKLY|MONTHLY)$")


def _filters_dict(payload: ExportJobRequest) -> dict:
    return ReportFilters(
        payload.date_from, payload.date_to, payload.status,
        payload.category_id, payload.warehouse_id,
    ).to_dict()


def _job_view(job: BackgroundJob) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "params": job.params,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "result_summary": job.result_summary,
        "error_message": job.error_message,
        "downloadable": bool(job.result_path and os.path.exists(job.result_path)),
        "schedule_id": job.schedule_id,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


# ---- background jobs (static paths first) -----------------------------------

@router.post("/jobs", status_code=202)
def create_export_job(
    payload: ExportJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    """Generate a report file in the background; poll /jobs/{id}."""
    _check_report(payload.report, current_user)
    job = create_job(
        db,
        "REPORT_EXPORT",
        {"report": payload.report, "format": payload.format, "filters": _filters_dict(payload)},
        current_user.id,
    )
    background_tasks.add_task(run_job, job.id)
    return _job_view(job)


@router.post("/jobs/notifications", status_code=202)
def create_notification_job(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*ADMIN_ROLES)),
):
    """Process queued email/SMS deliveries (and retry eligible failures)."""
    job = create_job(db, "NOTIFICATION_DISPATCH", {"limit": 100}, current_user.id)
    background_tasks.add_task(run_job, job.id)
    return _job_view(job)


@router.get("/jobs")
def list_jobs(
    job_status: str | None = Query(default=None, alias="status", pattern=r"^(PENDING|RUNNING|SUCCEEDED|FAILED)$"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    query = db.query(BackgroundJob)
    if current_user.role not in ADMIN_ROLES:
        query = query.filter(BackgroundJob.created_by == current_user.id)
    if job_status:
        query = query.filter(BackgroundJob.status == job_status)
    jobs = query.order_by(BackgroundJob.id.desc()).offset(skip).limit(limit).all()
    return [_job_view(j) for j in jobs]


def _own_job(db: Session, job_id: int, user: User) -> BackgroundJob:
    job = db.get(BackgroundJob, job_id)
    if job is None or (user.role not in ADMIN_ROLES and job.created_by != user.id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    return _job_view(_own_job(db, job_id, current_user))


@router.get("/jobs/{job_id}/download")
def download_job_result(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    job = _own_job(db, job_id, current_user)
    if job.status != "SUCCEEDED" or not job.result_path or not os.path.exists(job.result_path):
        raise HTTPException(status_code=409, detail="No result file is available for this job")
    fmt = (job.params or {}).get("format", "csv")
    return FileResponse(
        job.result_path,
        media_type=exports.FORMATS.get(fmt, "application/octet-stream"),
        filename=os.path.basename(job.result_path),
    )


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    job = _own_job(db, job_id, current_user)
    if job.status != "FAILED":
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")
    if job.attempts >= job.max_attempts:
        raise HTTPException(status_code=409, detail="Maximum attempts reached")
    background_tasks.add_task(run_job, job.id)
    return {"id": job.id, "status": "retry_scheduled", "attempts": job.attempts}


# ---- schedules -------------------------------------------------------------

@router.post("/schedules", status_code=201)
def create_schedule(
    payload: ScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    """Schedule a recurring inventory/order/etc. summary export."""
    _check_report(payload.report, current_user)
    schedule = ReportSchedule(
        report_name=payload.report,
        export_format=payload.format,
        filters=_filters_dict(payload),
        frequency=payload.frequency,
        next_run_at=_now() + FREQUENCY_DELTAS[payload.frequency],
        created_by=current_user.id,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return _schedule_view(schedule)


def _schedule_view(s: ReportSchedule) -> dict:
    return {
        "id": s.id, "report": s.report_name, "format": s.export_format,
        "filters": s.filters, "frequency": s.frequency,
        "next_run_at": s.next_run_at, "last_run_at": s.last_run_at,
        "is_active": s.is_active,
    }


@router.get("/schedules")
def list_schedules(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    query = db.query(ReportSchedule)
    if current_user.role not in ADMIN_ROLES:
        query = query.filter(ReportSchedule.created_by == current_user.id)
    return [_schedule_view(s) for s in query.order_by(ReportSchedule.id.desc()).all()]


@router.delete("/schedules/{schedule_id}", status_code=204)
def deactivate_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    schedule = db.get(ReportSchedule, schedule_id)
    if schedule is None or (
        current_user.role not in ADMIN_ROLES and schedule.created_by != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Schedule not found")
    schedule.is_active = False
    db.commit()


@router.post("/scheduler/run")
def run_scheduler_now(
    current_user: User = Depends(require_roles(*ADMIN_ROLES)),
):
    """Run one scheduler pass now (use from cron when the in-process
    scheduler is disabled)."""
    return scheduler_tick()


# ---- direct download -------------------------------------------------------

@router.get("/{report_name}.{fmt}")
def export_report(
    report_name: str,
    fmt: str,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = Query(default=None, max_length=30),
    category_id: int | None = Query(default=None, gt=0),
    warehouse_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*EXPORT_ROLES)),
):
    """Download a report immediately as csv, xlsx or pdf."""
    if fmt not in exports.FORMATS:
        raise HTTPException(status_code=404, detail="Format must be csv, xlsx or pdf")
    _check_report(report_name, current_user)
    result = build_report(db, report_name, _filters(date_from, date_to, status, category_id, warehouse_id))
    return Response(
        content=exports.render(result, fmt),
        media_type=exports.FORMATS[fmt],
        headers={"Content-Disposition": f'attachment; filename="{report_name}.{fmt}"'},
    )
