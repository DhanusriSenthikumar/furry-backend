from pydantic import BaseModel, Field


class QuestionCreate(BaseModel):
    body: str = Field(min_length=5, max_length=500)


class AnswerCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class AnswerOut(BaseModel):
    body: str
    answered_by: str
    answered_at: str


class QuestionOut(BaseModel):
    id: str
    product_id: str
    # Both carried so the admin answer queue can name and link the product
    # without a second round-trip per row.
    product_name: str
    product_slug: str = ""
    user_id: str
    user_name: str
    body: str
    created_at: str
    answer: AnswerOut | None = None
