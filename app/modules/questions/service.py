from datetime import datetime, timezone

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.modules.notifications.service import NotificationService
from app.modules.products.repository import ProductRepository
from app.modules.questions.repository import QuestionRepository
from app.modules.questions.schemas import AnswerCreate, QuestionCreate


class QuestionService:
    """Customer questions on a product, answered by staff. Unlike reviews there
    is no one-per-customer rule — a shopper may ask about sizing today and
    ingredients tomorrow."""

    def __init__(
        self,
        repo: QuestionRepository,
        products: ProductRepository,
        notifications: NotificationService | None = None,
    ):
        self.repo = repo
        self.products = products
        # An answer nobody sees is no answer. Whoever asked gets told, since
        # they have no reason to keep re-opening the product page to check.
        self.notifications = notifications

    async def list_for_product(self, product_id: str) -> list[dict]:
        return await self.repo.find_by_product(product_id)

    async def list_unanswered(self) -> list[dict]:
        return await self.repo.find_unanswered()

    async def count_unanswered(self) -> int:
        return await self.repo.count_unanswered()

    async def ask(self, product_id: str, user: dict, payload: QuestionCreate) -> dict:
        product = await self.products.find_by_id(product_id)
        if not product:
            raise NotFoundError("Product not found")

        return await self.repo.insert(
            {
                "product_id": product_id,
                "product_name": product["name"],
                "product_slug": product.get("slug", ""),
                "user_id": str(user["_id"]),
                "user_name": user["name"],
                "body": payload.body.strip(),
                "created_at": datetime.now(timezone.utc),
                "answer": None,
            }
        )

    async def answer(self, question_id: str, admin: dict, payload: AnswerCreate) -> dict:
        question = await self.repo.find_by_id(question_id)
        if not question:
            raise NotFoundError("Question not found")

        answer = {
            "body": payload.body.strip(),
            "answered_by": admin["name"],
            "answered_at": datetime.now(timezone.utc),
        }
        updated = await self.repo.update_by_id(question_id, {"answer": answer})

        # Keyed on the question, so correcting a typo in an answer doesn't tell
        # the customer twice.
        if self.notifications is not None and not question.get("answer"):
            await self.notifications.question_answered(updated)
        return updated

    async def delete(self, question_id: str, user: dict) -> None:
        question = await self.repo.find_by_id(question_id)
        if not question:
            raise NotFoundError("Question not found")
        if question["user_id"] != str(user["_id"]) and not user.get("is_admin"):
            raise ForbiddenError("Not allowed to delete this question")
        # Once staff have answered publicly, the thread is part of the product
        # page for every other shopper — only an admin can take it down.
        if question.get("answer") and not user.get("is_admin"):
            raise ValidationError("An answered question can only be removed by staff")

        await self.repo.delete_by_id(question_id)
