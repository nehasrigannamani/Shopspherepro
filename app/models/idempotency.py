from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class IdempotencyKey(Base):
    """One row per (scope, key). The unique constraint is what makes a
    duplicate request detectable even under concurrency."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # IN_PROGRESS -> COMPLETED
    status: Mapped[str] = mapped_column(String(20), default="IN_PROGRESS", nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
