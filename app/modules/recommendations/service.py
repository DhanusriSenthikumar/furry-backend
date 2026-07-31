from collections import Counter

from app.modules.orders.repository import OrderRepository
from app.modules.pets.repository import PetRepository
from app.modules.products.repository import ProductRepository
from app.modules.recommendations.repository import ProductViewRepository

# Life-stage words as they appear in product copy. A mismatch is a *negative*
# signal rather than a neutral one: senior-formula food is actively wrong for a
# household of puppies, so it should rank below a product that says nothing.
LIFE_STAGE_WORDS = {
    "baby": {"puppy", "kitten", "junior", "baby"},
    "adult": {"adult"},
    "senior": {"senior", "mature", "ageing", "aging"},
}
LIFE_STAGE_LABEL = {"baby": "young", "adult": "adult", "senior": "senior"}

SIZE_WORDS = {
    "small": {"small breed", "toy breed", "small dog", "for small"},
    "medium": {"medium breed", "medium dog"},
    "large": {"large breed", "giant breed", "large dog", "for large"},
}

# Categories a household runs out of. Having bought one before is a reason to
# show it again; having bought a bed or a carrier is a reason not to.
CONSUMABLE_CATEGORIES = {"food", "treats", "grooming"}
CONSUMABLE_WORDS = {"food", "treat", "litter", "shampoo", "wipes", "supplement", "chew"}

# Orders in these states never happened as far as taste is concerned.
IGNORED_ORDER_STATUSES = {"cancelled", "payment_failed"}


def life_stage(age_years: float) -> str:
    if age_years and age_years < 1:
        return "baby"
    if age_years and age_years >= 8:
        return "senior"
    return "adult"


def size_band(weight_kg: float) -> str | None:
    if not weight_kg:
        return None
    if weight_kg < 10:
        return "small"
    if weight_kg <= 25:
        return "medium"
    return "large"


class RecommendationService:
    """Ranks the shelf against everything known about a customer: the pets on
    their profile (type, breed, age, size), what they have ordered before, and
    what they have been looking at. Each pick carries the signal that earned it,
    so the storefront can explain itself instead of just asserting a match."""

    def __init__(
        self,
        products: ProductRepository,
        pets: PetRepository,
        orders: OrderRepository,
        views: ProductViewRepository,
    ):
        self.products = products
        self.pets = pets
        self.orders = orders
        self.views = views

    async def record_view(self, user_id: str, product_id: str) -> None:
        await self.views.record(user_id, product_id)

    async def recently_viewed(self, user_id: str, limit: int = 12) -> list[dict]:
        views = await self.views.recent_for_user(user_id, limit=limit)
        return await self.products.find_by_ids([v["product_id"] for v in views])

    async def recommend(self, user: dict, limit: int = 8) -> dict:
        user_id = str(user["_id"])
        pets = await self.pets.find_by_owner(user_id)
        orders = await self.orders.find_by_user(user_id)
        views = await self.views.recent_for_user(user_id, limit=20)

        pet_types = list(dict.fromkeys(pet["pet_type"] for pet in pets))
        pet_names = [pet["name"] for pet in pets]

        purchased_ids: list[str] = []
        for order in orders:
            if order.get("status") in IGNORED_ORDER_STATUSES:
                continue
            purchased_ids.extend(item["product_id"] for item in order.get("items", []))
        viewed_ids = [view["product_id"] for view in views]

        # Resolve history to real products so affinities key off live category and
        # brand data rather than the name snapshot frozen into the order lines.
        history = await self.products.find_by_ids(list(dict.fromkeys(purchased_ids + viewed_ids)))
        by_id = {str(doc["_id"]): doc for doc in history}

        purchased_categories = Counter(
            by_id[pid]["category_id"] for pid in purchased_ids if pid in by_id and by_id[pid].get("category_id")
        )
        purchased_brands = Counter(
            by_id[pid]["brand"] for pid in purchased_ids if pid in by_id and by_id[pid].get("brand")
        )
        viewed_categories = Counter(
            by_id[pid]["category_id"] for pid in viewed_ids if pid in by_id and by_id[pid].get("category_id")
        )

        candidates = await self.products.find_recommendation_candidates(
            pet_types=pet_types,
            category_ids=list(purchased_categories) + list(viewed_categories),
            brands=list(purchased_brands),
        )

        scored: list[tuple[float, dict, str]] = []
        for product in candidates:
            score, reason = self._score(
                product,
                pets=pets,
                purchased_ids=set(purchased_ids),
                purchased_categories=purchased_categories,
                purchased_brands=purchased_brands,
                viewed_categories=viewed_categories,
            )
            if score > 0:
                scored.append((score, product, reason))

        scored.sort(key=lambda row: (-row[0], -row[1].get("rating", 0.0), -row[1].get("rating_count", 0)))

        return {
            "pet_types": pet_types,
            "pet_names": pet_names,
            "items": [{"product": product, "reason": reason} for _, product, reason in scored[:limit]],
            "personalized": bool(pets or purchased_ids or viewed_ids),
        }

    @staticmethod
    def _is_consumable(product: dict) -> bool:
        category = (product.get("category_name") or "").strip().lower()
        if category in CONSUMABLE_CATEGORIES:
            return True
        name = (product.get("name") or "").lower()
        return any(word in name for word in CONSUMABLE_WORDS)

    def _score(
        self,
        product: dict,
        pets: list[dict],
        purchased_ids: set[str],
        purchased_categories: Counter,
        purchased_brands: Counter,
        viewed_categories: Counter,
    ) -> tuple[float, str]:
        """Returns (score, reason). A score of 0 drops the product entirely."""
        product_id = str(product["_id"])
        text = f"{product.get('name', '')} {product.get('description', '')}".lower()

        # (weight, reason) pairs; the heaviest positive one becomes the caption.
        signals: list[tuple[float, str]] = [(0.2, "Popular in the shop")]

        suitable = set(product.get("suitable_pet_types") or [])
        matched = [pet for pet in pets if pet["pet_type"] in suitable]
        if suitable and pets and not matched:
            return 0.0, ""  # sold for animals this household doesn't have
        if matched:
            names = ", ".join(pet["name"] for pet in matched[:2])
            signals.append((3.0 + 0.5 * (len(matched) - 1), f"Suits {names}"))

        relevant = matched or pets

        # Life stage — the strongest correction available, because getting it
        # wrong is worse than showing something generic.
        mentioned_stages = {
            stage for stage, words in LIFE_STAGE_WORDS.items() if any(word in text for word in words)
        }
        if mentioned_stages and relevant:
            wanted = {life_stage(pet.get("age_years", 0)) for pet in relevant}
            overlap = mentioned_stages & wanted
            if overlap:
                stage = next(iter(overlap))
                signals.append((1.4, f"Formulated for {LIFE_STAGE_LABEL[stage]} pets"))
            else:
                signals.append((-2.5, ""))

        # Size — same shape as life stage, but a softer correction.
        mentioned_sizes = {size for size, words in SIZE_WORDS.items() if any(word in text for word in words)}
        if mentioned_sizes and relevant:
            wanted_sizes = {size_band(pet.get("weight_kg", 0)) for pet in relevant} - {None}
            if wanted_sizes:
                if mentioned_sizes & wanted_sizes:
                    signals.append((0.9, f"Sized for {next(iter(mentioned_sizes & wanted_sizes))} breeds"))
                else:
                    signals.append((-1.2, ""))

        for pet in relevant:
            breed = (pet.get("breed") or "").strip().lower()
            if breed and breed in text:
                signals.append((1.6, f"Made for {breed.title()}s like {pet['name']}"))
                break

        category_id = product.get("category_id")
        if category_id and category_id in purchased_categories:
            repeat = min(purchased_categories[category_id], 3)
            label = product.get("category_name") or "this range"
            signals.append((0.8 + 0.4 * repeat, f"You order {label} often"))
        elif category_id and category_id in viewed_categories:
            label = product.get("category_name") or "these"
            signals.append((0.7, f"Like the {label} you were browsing"))

        brand = product.get("brand")
        if brand and brand in purchased_brands:
            signals.append((1.0, f"{brand}, a brand you buy"))

        if product_id in purchased_ids:
            if not self._is_consumable(product):
                return 0.0, ""  # they already own it and won't need a second
            signals.append((1.3, "Time to restock"))

        rating = product.get("rating", 0.0) or 0.0
        if rating >= 4 and product.get("rating_count", 0) >= 3:
            signals.append((rating / 5 * 1.2, f"Rated {rating:.1f}★ by other owners"))

        compare_at = product.get("compare_at_price", 0) or 0
        price = product.get("price", 0) or 0
        if compare_at > price > 0:
            signals.append((0.6, f"{round((1 - price / compare_at) * 100)}% off right now"))

        score = sum(weight for weight, _ in signals)
        reason = max((s for s in signals if s[1]), key=lambda s: s[0])[1]
        return round(score, 3), reason
