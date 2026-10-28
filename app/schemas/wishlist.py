from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WishlistItemCreate(BaseModel):
    product_id: int


class WishlistItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    product_id: int
    created_at: datetime


class WishlistProductResponse(BaseModel):
    wishlist_item_id: int
    product_id: int
    name: str
    sku: str
    price: float
    image_url: str | None
    created_at: datetime