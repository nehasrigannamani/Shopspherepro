
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated


ReviewTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=150),
]

ReviewText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10, max_length=5000),
]


class ProductReviewCreate(BaseModel):
    product_id: int = Field(gt=0)
    order_item_id: int = Field(gt=0)
    rating: int = Field(ge=1, le=5)
    title: ReviewTitle
    review_text: ReviewText


class ReviewModerationUpdate(BaseModel):
    status: str = Field(pattern=r"^(APPROVED|REJECTED)$")
    moderation_note: str | None = Field(default=None, max_length=2000)


class ProductReviewResponse(BaseModel):
    id: int
    product_id: int
    user_id: int
    order_item_id: int
    rating: int
    title: str
    review_text: str
    status: str
    moderation_note: str | None
    moderated_by: int | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
