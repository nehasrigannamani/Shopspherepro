
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.product import Product
from app.models.product_question import ProductQuestion
from app.models.user import User
from app.schemas.product_questions import (
    ProductQuestionAnswer,
    ProductQuestionCreate,
    ProductQuestionResponse,
)


router = APIRouter(
    prefix="/questions",
    tags=["Product Q&A"],
)


STAFF_ROLES = (
    "SUPER_ADMIN",
    "ADMIN",
    "PRODUCT_MANAGER",
    "SUPPORT_AGENT",
)


@router.post(
    "",
    response_model=ProductQuestionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product_question(
    payload: ProductQuestionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = (
        db.query(Product)
        .filter(Product.id == payload.product_id)
        .first()
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    question = ProductQuestion(
        product_id=payload.product_id,
        user_id=current_user.id,
        question_text=payload.question_text,
        status="OPEN",
    )

    db.add(question)
    db.commit()
    db.refresh(question)

    return question


@router.get(
    "/products/{product_id}",
    response_model=list[ProductQuestionResponse],
)
def get_product_questions(
    product_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    product = (
        db.query(Product.id)
        .filter(Product.id == product_id)
        .first()
    )

    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    return (
        db.query(ProductQuestion)
        .filter(
            ProductQuestion.product_id == product_id,
            ProductQuestion.status == "ANSWERED",
        )
        .order_by(ProductQuestion.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get(
    "/mine",
    response_model=list[ProductQuestionResponse],
)
def get_my_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(ProductQuestion)
        .filter(ProductQuestion.user_id == current_user.id)
        .order_by(ProductQuestion.created_at.desc())
        .all()
    )


@router.get(
    "",
    response_model=list[ProductQuestionResponse],
)
def list_questions_for_staff(
    question_status: str = Query(
        default="OPEN",
        pattern=r"^(OPEN|ANSWERED|ALL)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    query = db.query(ProductQuestion)

    if question_status != "ALL":
        query = query.filter(
            ProductQuestion.status == question_status
        )

    return (
        query.order_by(ProductQuestion.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.patch(
    "/{question_id}/answer",
    response_model=ProductQuestionResponse,
)
def answer_product_question(
    question_id: int,
    payload: ProductQuestionAnswer,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*STAFF_ROLES)),
):
    question = (
        db.query(ProductQuestion)
        .filter(ProductQuestion.id == question_id)
        .first()
    )

    if question is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        )

    if question.status == "ANSWERED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This question has already been answered",
        )

    question.answer_text = payload.answer_text
    question.answered_by = current_user.id
    question.status = "ANSWERED"

    db.commit()
    db.refresh(question)

    return question


@router.delete(
    "/{question_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_product_question(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    question = (
        db.query(ProductQuestion)
        .filter(ProductQuestion.id == question_id)
        .first()
    )

    if question is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        )

    is_staff = current_user.role in STAFF_ROLES

    if question.user_id != current_user.id and not is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own questions",
        )

    if question.status == "ANSWERED" and not is_staff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Answered questions cannot be deleted by customers",
        )

    db.delete(question)
    db.commit()

    return None
