from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BackgroundJob(Base):
    """Persistent record of every background task and its outcome."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        Index("ix_background_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # REPORT_EXPORT, NOTIFICATION_DISPATCH, SCHEDULED_SUMMARY
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # PENDING, RUNNING, SUCCEEDED, FAILED
    status: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False
    )
    params: Mapped[dict | None] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    result_path: Mapped[str | None] = mapped_column(String(500))
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("report_schedules.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_name: Mapped[str] = mapped_column(String(50), nullable=False)
    export_format: Mapped[str] = mapped_column(
        String(10), default="csv", nullable=False
    )
    filters: Mapped[dict | None] = mapped_column(JSON)
    # DAILY, WEEKLY, MONTHLY
    frequency: Mapped[str] = mapped_column(String(10), nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
