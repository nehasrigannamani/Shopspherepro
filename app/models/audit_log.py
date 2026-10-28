
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.sql import func

from app.db.session import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)

    actor_user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(100), nullable=False, index=True)
    entity_id = Column(String(100), nullable=True)
    outcome = Column(String(20), nullable=False, default="SUCCESS")
    details = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    request_id = Column(String(100), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
