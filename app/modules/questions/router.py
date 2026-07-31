from fastapi import APIRouter, Depends

from app.deps import get_current_admin, get_current_user, get_db
from app.modules.products.repository import ProductRepository
from app.modules.questions.repository import QuestionRepository
from app.modules.questions.schemas import AnswerCreate, AnswerOut, QuestionCreate, QuestionOut
from app.modules.questions.service import QuestionService

router = APIRouter(tags=["questions"])


def _service(db=Depends(get_db)) -> QuestionService:
    return QuestionService(QuestionRepository(db), ProductRepository(db))


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else value


def question_out(doc: dict) -> QuestionOut:
    answer = doc.get("answer")
    return QuestionOut(
        id=str(doc["_id"]),
        product_id=doc["product_id"],
        product_name=doc.get("product_name", ""),
        product_slug=doc.get("product_slug", ""),
        user_id=doc["user_id"],
        user_name=doc["user_name"],
        body=doc["body"],
        created_at=_iso(doc["created_at"]),
        answer=(
            AnswerOut(
                body=answer["body"],
                answered_by=answer.get("answered_by", "Furry Friends"),
                answered_at=_iso(answer["answered_at"]),
            )
            if answer
            else None
        ),
    )


@router.get("/products/{product_id}/questions", response_model=list[QuestionOut])
async def list_product_questions(product_id: str, service: QuestionService = Depends(_service)):
    """Public: the Q&A thread shoppers read before buying."""
    questions = await service.list_for_product(product_id)
    return [question_out(q) for q in questions]


@router.post("/products/{product_id}/questions", response_model=QuestionOut, status_code=201)
async def ask_product_question(
    product_id: str,
    payload: QuestionCreate,
    user: dict = Depends(get_current_user),
    service: QuestionService = Depends(_service),
):
    question = await service.ask(product_id, user, payload)
    return question_out(question)


@router.get("/questions/unanswered", response_model=list[QuestionOut])
async def list_unanswered_questions(
    _admin: dict = Depends(get_current_admin), service: QuestionService = Depends(_service)
):
    """The staff answer queue, oldest question first."""
    questions = await service.list_unanswered()
    return [question_out(q) for q in questions]


@router.post("/questions/{question_id}/answer", response_model=QuestionOut)
async def answer_question(
    question_id: str,
    payload: AnswerCreate,
    admin: dict = Depends(get_current_admin),
    service: QuestionService = Depends(_service),
):
    question = await service.answer(question_id, admin, payload)
    return question_out(question)


@router.delete("/questions/{question_id}", status_code=204)
async def delete_question(
    question_id: str,
    user: dict = Depends(get_current_user),
    service: QuestionService = Depends(_service),
):
    await service.delete(question_id, user)
