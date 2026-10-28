
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    __table_args__ = (
        Index(
            "ix_order_status_history_order_created",
            "order_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    new_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    changed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )

    note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
