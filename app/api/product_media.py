
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.product import Product
from app.models.product_media import MediaType, ProductMedia
from app.models.product_variant import ProductVariant
from app.models.user import User, UserRole
from app.schemas.product_media import ProductMediaResponse


router = APIRouter(prefix="/products", tags=["Product Media"])

MANAGER_ROLES = (
    UserRole.SUPER_ADMIN,
    UserRole.ADMIN,
    UserRole.PRODUCT_MANAGER,
)

MAX_FILE_SIZE = 5 * 1024 * 1024
STORAGE_ROOT = Path("storage/product_media").resolve()

ALLOWED_TYPES = {
    "image/jpeg": (".jpg", "IMAGE"),
    "image/png": (".png", "IMAGE"),
    "image/webp": (".webp", "IMAGE"),
    "application/pdf": (".pdf", "DOCUMENT"),
}


def validate_file_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")

    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")

    if content_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )

    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")

    return False


def get_active_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)

    if product is None or not product.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active product not found",
        )

    return product


def get_media_or_404(
    db: Session,
    product_id: int,
    media_id: int,
) -> ProductMedia:
    media = db.scalar(
        select(ProductMedia).where(
            ProductMedia.id == media_id,
            ProductMedia.product_id == product_id,
        )
    )

    if media is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product media not found",
        )

    return media


def resolve_storage_path(storage_path: str) -> Path:
    path = Path(storage_path).resolve()

    if not path.is_relative_to(STORAGE_ROOT):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid stored file path",
        )

    return path


@router.post(
    "/{product_id}/media",
    response_model=ProductMediaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_product_media(
    product_id: int,
    file: UploadFile = File(...),
    media_type: MediaType = Form(...),
    variant_id: int | None = Form(default=None),
    alt_text: str | None = Form(default=None, max_length=300),
    display_order: int = Form(default=0, ge=0, le=10000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    product = get_active_product(db, product_id)

    if variant_id is not None:
        variant = db.scalar(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.product_id == product_id,
                ProductVariant.is_active.is_(True),
            )
        )
        if variant is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Active variant does not belong to this product",
            )

    content_type = (file.content_type or "").lower().strip()
    allowed = ALLOWED_TYPES.get(content_type)

    if allowed is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, WebP images and PDF documents are allowed",
        )

    extension, expected_media_type = allowed

    if media_type.value != expected_media_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="media_type does not match the uploaded file type",
        )

    content = await file.read(MAX_FILE_SIZE + 1)

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File size must not exceed 5 MB",
        )

    if not validate_file_signature(content_type, content):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The file content does not match its declared type",
        )

    original_name = Path(file.filename or "upload").name[:255]
    generated_name = f"{uuid4().hex}{extension}"

    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = STORAGE_ROOT / generated_name
    destination.write_bytes(content)

    relative_path = destination.relative_to(Path.cwd().resolve()).as_posix()

    media = ProductMedia(
        product_id=product.id,
        variant_id=variant_id,
        media_type=media_type,
        file_name=original_name,
        content_type=content_type,
        file_size=len(content),
        storage_path=relative_path,
        alt_text=alt_text.strip() if alt_text else None,
        display_order=display_order,
    )

    db.add(media)

    try:
        db.commit()
        db.refresh(media)
    except (IntegrityError, Exception):
        db.rollback()
        destination.unlink(missing_ok=True)
        raise

    return media


@router.get(
    "/{product_id}/media",
    response_model=list[ProductMediaResponse],
)
def list_product_media(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_active_product(db, product_id)

    statement = (
        select(ProductMedia)
        .where(ProductMedia.product_id == product_id)
        .order_by(ProductMedia.display_order, ProductMedia.id)
    )

    return list(db.scalars(statement).all())


@router.get("/{product_id}/media/{media_id}/file")
def retrieve_product_media_file(
    product_id: int,
    media_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    get_active_product(db, product_id)
    media = get_media_or_404(db, product_id, media_id)

    if (
        media.media_type == MediaType.DOCUMENT
        and current_user.role not in MANAGER_ROLES
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to retrieve this document",
        )

    path = resolve_storage_path(media.storage_path)

    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored file is missing",
        )

    return FileResponse(
        path=path,
        media_type=media.content_type,
        filename=media.file_name,
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/{product_id}/media/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_product_media(
    product_id: int,
    media_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*MANAGER_ROLES)),
):
    get_active_product(db, product_id)
    media = get_media_or_404(db, product_id, media_id)
    path = resolve_storage_path(media.storage_path)

    db.delete(media)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    path.unlink(missing_ok=True)
    return None
