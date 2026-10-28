
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.product_media import MediaType


class ProductMediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    variant_id: int | None
    media_type: MediaType
    file_name: str
    content_type: str
    file_size: int
    alt_text: str | None
    display_order: int
    created_at: datetime


class ProductMediaUploadMetadata(BaseModel):
    alt_text: str | None = Field(default=None, max_length=300)
    display_order: int = Field(default=0, ge=0, le=10000)
