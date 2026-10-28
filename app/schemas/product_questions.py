
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


QuestionText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=3000,
    ),
]

AnswerText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=5000,
    ),
]


class ProductQuestionCreate(BaseModel):
    product_id: int = Field(gt=0)
    question_text: QuestionText


class ProductQuestionAnswer(BaseModel):
    answer_text: AnswerText


class ProductQuestionResponse(BaseModel):
    id: int
    product_id: int
    user_id: int
    question_text: str
    answer_text: str | None
    answered_by: int | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
