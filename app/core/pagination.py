"""Shared pagination helpers with a hard page-size cap."""
from dataclasses import dataclass
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

MAX_PAGE_SIZE = 100
T = TypeVar("T")


@dataclass
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def paginate(db: Session, stmt, params: PageParams) -> tuple[list, int]:
    """Return (rows, total) for a SELECT of ORM entities."""
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.limit(params.page_size).offset(params.offset)).all()
    return list(rows), int(total)
