
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class MediaType(str, Enum):
    IMAGE = "IMAGE"
    DOCUMENT = "DOCUMENT"


class ProductMedia(Base):
    __tablename__ = "product_media"

    id: Mapped[int] = mapped_column(primary_key=True)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    media_type: Mapped[MediaType] = mapped_column(
        SQLAlchemyEnum(MediaType, name="product_media_type"),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    content_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    file_size: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    storage_path: Mapped[str] = mapped_column(
        String(2048), nullable=False
    )
    alt_text: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    display_order: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
