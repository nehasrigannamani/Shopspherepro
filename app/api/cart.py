from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.db.session import get_db
from app.models.cart import Cart, CartItem
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.user import User, UserRole
from app.models.variant_inventory import VariantInventory
from app.schemas.cart import (
    CartItemAdd,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
)


router = APIRouter(prefix="/cart", tags=["Shopping Cart"])


def get_or_create_cart(db: Session, user_id: int) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))

    if cart is not None:
        return cart

    cart = Cart(user_id=user_id)
    db.add(cart)

    try:
        db.commit()
        db.refresh(cart)
    except IntegrityError:
        db.rollback()
        cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
        if cart is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not create the cart. Please try again.",
            ) from None

    return cart


def lock_cart(db: Session, cart: Cart) -> None:
    """Serialise concurrent cart changes for one customer."""
    db.scalar(select(Cart.id).where(Cart.id == cart.id).with_for_update())


def item_filter(cart_id: int, product_id: int, variant_id: int | None):
    condition = [CartItem.cart_id == cart_id, CartItem.product_id == product_id]
    if variant_id is None:
        condition.append(CartItem.variant_id.is_(None))
    else:
        condition.append(CartItem.variant_id == variant_id)
    return condition


def build_cart_response(db: Session, cart: Cart) -> CartResponse:
    rows = db.execute(
        select(CartItem, Product)
        .join(Product, Product.id == CartItem.product_id)
        .where(CartItem.cart_id == cart.id)
        .order_by(CartItem.id)
    ).all()

    response_items = []
    subtotal = Decimal("0.00")
    total_items = 0

    for item, product in rows:
        variant = (
            db.get(ProductVariant, item.variant_id)
            if item.variant_id is not None
            else None
        )
        unit_price = (
            variant.price
            if variant is not None and variant.price is not None
            else product.price
        )
        line_total = unit_price * item.quantity
        subtotal += line_total
        total_items += item.quantity

        response_items.append(
            CartItemResponse(
                product_id=product.id,
                variant_id=item.variant_id,
                variant_sku=variant.sku if variant else None,
                variant_options=variant.option_values if variant else None,
                sku=product.sku,
                name=product.name,
                quantity=item.quantity,
                unit_price=unit_price,
                line_total=line_total,
            )
        )

    return CartResponse(
        id=cart.id,
        user_id=cart.user_id,
        items=response_items,
        total_items=total_items,
        subtotal=subtotal,
        created_at=cart.created_at,
        updated_at=cart.updated_at,
    )


def resolve_variant(
    db: Session, product: Product, variant_id: int | None
) -> ProductVariant | None:
    """Validate the variant choice for a product (None = no variants)."""
    active_variants = db.scalar(
        select(func.count(ProductVariant.id)).where(
            ProductVariant.product_id == product.id,
            ProductVariant.is_active.is_(True),
        )
    )

    if variant_id is None:
        if active_variants:
            raise HTTPException(
                status_code=422,
                detail="This product has variants; variant_id is required",
            )
        return None

    variant = db.get(ProductVariant, variant_id)
    if variant is None or variant.product_id != product.id or not variant.is_active:
        raise HTTPException(status_code=404, detail="Active variant not found")
    return variant


def locked_stock(db: Session, product_id: int, variant_id: int | None):
    if variant_id is not None:
        stock = db.scalar(
            select(VariantInventory)
            .where(VariantInventory.variant_id == variant_id)
            .with_for_update()
        )
    else:
        stock = db.scalar(
            select(Inventory)
            .where(Inventory.product_id == product_id)
            .with_for_update()
        )
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This item has no inventory record",
        )
    return stock


def check_stock(
    db: Session,
    product_id: int,
    variant_id: int | None,
    requested_quantity: int,
    cart_id: int,
) -> None:
    stock = locked_stock(db, product_id, variant_id)

    existing_item = db.scalar(
        select(CartItem).where(*item_filter(cart_id, product_id, variant_id))
    )
    already_in_cart = existing_item.quantity if existing_item else 0
    available = stock.quantity_on_hand - stock.reserved_quantity

    if requested_quantity + already_in_cart > available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Insufficient available stock. Available quantity: {available}",
        )


@router.get("", response_model=CartResponse)
def get_my_cart(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    cart = get_or_create_cart(db, current_user.id)
    return build_cart_response(db, cart)


@router.post("/items", response_model=CartResponse)
def add_cart_item(
    payload: CartItemAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    cart = get_or_create_cart(db, current_user.id)
    lock_cart(db, cart)

    product = db.scalar(
        select(Product).where(
            Product.id == payload.product_id,
            Product.is_active.is_(True),
        )
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    variant = resolve_variant(db, product, payload.variant_id)
    variant_id = variant.id if variant else None

    check_stock(db, product.id, variant_id, payload.quantity, cart.id)

    item = db.scalar(
        select(CartItem)
        .where(*item_filter(cart.id, product.id, variant_id))
        .with_for_update()
    )

    if item is None:
        db.add(
            CartItem(
                cart_id=cart.id,
                product_id=product.id,
                variant_id=variant_id,
                quantity=payload.quantity,
            )
        )
    else:
        item.quantity += payload.quantity

    try:
        db.commit()
        db.refresh(cart)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cart update conflicted with another request. Please retry.",
        ) from None

    return build_cart_response(db, cart)


@router.put("/items/{product_id}", response_model=CartResponse)
def update_cart_item(
    product_id: int,
    payload: CartItemUpdate,
    variant_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    cart = get_or_create_cart(db, current_user.id)
    lock_cart(db, cart)

    item = db.scalar(
        select(CartItem)
        .where(*item_filter(cart.id, product_id, variant_id))
        .with_for_update()
    )

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in your cart",
        )

    product = db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.is_active.is_(True),
        )
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This product is no longer available",
        )

    stock = locked_stock(db, product_id, variant_id)
    available = stock.quantity_on_hand - stock.reserved_quantity

    if payload.quantity > available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Insufficient available stock. Available quantity: {available}",
        )

    item.quantity = payload.quantity
    db.commit()
    db.refresh(cart)

    return build_cart_response(db, cart)


@router.delete("/items/{product_id}", response_model=CartResponse)
def remove_cart_item(
    product_id: int,
    variant_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    cart = get_or_create_cart(db, current_user.id)
    lock_cart(db, cart)

    item = db.scalar(
        select(CartItem).where(*item_filter(cart.id, product_id, variant_id))
    )

    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in your cart",
        )

    db.delete(item)
    db.commit()
    db.refresh(cart)

    return build_cart_response(db, cart)


@router.delete("", response_model=CartResponse)
def clear_cart(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.CUSTOMER)),
):
    cart = get_or_create_cart(db, current_user.id)
    lock_cart(db, cart)

    items = db.scalars(
        select(CartItem).where(CartItem.cart_id == cart.id)
    ).all()

    for item in items:
        db.delete(item)

    db.commit()
    db.refresh(cart)

    return build_cart_response(db, cart)
