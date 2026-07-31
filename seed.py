"""Seeds the database with categories, users, sample products, sales, product
questions, and coupons.

Run with: python seed.py
Requires MONGODB_URI to be set in .env (left empty by default).
"""

import asyncio
import sys
from datetime import datetime, timezone

from app.core.security import hash_password
from app.db.mongodb import mongodb

ADMIN_EMAIL = "admin@petstore.test"
ADMIN_PASSWORD = "AdminPass123!"

# A ready-made customer so recommendations, Q&A and the MCP customer tools are
# demoable without signing up first.
SHOPPER_EMAIL = "shopper@petstore.test"
SHOPPER_PASSWORD = "ShopperPass123!"
SHOPPER_PETS = [
    {
        "name": "Rex",
        "pet_type": "dog",
        "breed": "Labrador",
        "age_years": 0.6,
        "weight_kg": 12.0,
        "gender": "male",
        "special_requirements": "Sensitive stomach — grain-free only.",
    },
    {
        "name": "Mochi",
        "pet_type": "cat",
        "breed": "British Shorthair",
        "age_years": 9.0,
        "weight_kg": 5.5,
        "gender": "female",
        "special_requirements": "",
    },
]

# slug -> the price it used to sell at. Anything not listed is full price. The
# API stores 0 unless this is genuinely above the selling price, so a sale badge
# always means a real saving.
SALE_PRICES = {
    "grain-free-dry-dog-food-15lb": 49.99,
    "wet-cat-food-pate-24ct": 34.99,
    "freeze-dried-dog-treats": 19.99,
    "slicker-brush": 13.99,
    "cat-harness-leash-set": 21.99,
    "fiddle-leaf-fig": 79.99,
    "monstera-deliciosa": 54.99,
    "self-watering-pot-8in": 39.99,
    "herb-garden-seed-kit": 28.99,
}

# (product_slug, question, answer or None — an unanswered one seeds the admin queue)
QUESTIONS = [
    (
        "grain-free-dry-dog-food-15lb",
        "Is this suitable for a puppy, or is it adult formula only?",
        "It's an all-life-stages recipe, so it's fine for puppies from 12 weeks. Feed to the puppy chart on the back of the bag.",
    ),
    (
        "monstera-deliciosa",
        "How big is the plant when it arrives?",
        "They ship at roughly 40–50cm tall in a 17cm nursery pot, usually with three or four mature leaves.",
    ),
    (
        "cat-harness-leash-set",
        "Will this fit a large British Shorthair?",
        "Yes — the chest strap adjusts from 28cm to 45cm, which covers most adult British Shorthairs.",
    ),
    (
        "oatmeal-shampoo-dogs",
        "Can I use this on a dog with a flea treatment applied?",
        None,
    ),
    (
        "self-watering-pot-8in",
        "Does the reservoir need emptying over winter?",
        None,
    ),
]

CATEGORIES = [
    {"name": "Food", "slug": "food", "description": "Everyday meals for every pet.", "kind": "pet"},
    {"name": "Treats", "slug": "treats", "description": "Snacks and rewards.", "kind": "pet"},
    {"name": "Grooming", "slug": "grooming", "description": "Shampoos, brushes, and grooming tools.", "kind": "pet"},
    {"name": "Toys", "slug": "toys", "description": "Toys to keep pets entertained.", "kind": "pet"},
    {"name": "Accessories", "slug": "accessories", "description": "Collars, leashes, bowls, and more.", "kind": "pet"},
    {
        "name": "Indoor Plants",
        "slug": "indoor-plants",
        "description": "Houseplants that thrive in living rooms, desks, and shelves.",
        "kind": "plant",
    },
    {
        "name": "Outdoor Plants",
        "slug": "outdoor-plants",
        "description": "Balcony, patio, and garden plants built for sun and weather.",
        "kind": "plant",
    },
    {
        "name": "Succulents & Cacti",
        "slug": "succulents-cacti",
        "description": "Low-water plants that forgive a missed watering.",
        "kind": "plant",
    },
    {
        "name": "Herbs & Edibles",
        "slug": "herbs-edibles",
        "description": "Kitchen herbs and edible greens you can grow at home.",
        "kind": "plant",
    },
    {
        "name": "Seeds & Bulbs",
        "slug": "seeds-bulbs",
        "description": "Start from scratch with seeds, bulbs, and growing kits.",
        "kind": "plant",
    },
    {
        "name": "Planters & Pots",
        "slug": "planters-pots",
        "description": "Ceramic, terracotta, and hanging planters in every size.",
        "kind": "plant",
    },
    {
        "name": "Soil & Plant Care",
        "slug": "soil-plant-care",
        "description": "Potting mixes, fertilisers, and tools to keep plants healthy.",
        "kind": "plant",
    },
]

COUPONS = [
    {
        "code": "WELCOME10",
        "description": "10% off your first order",
        "discount_type": "percent",
        "value": 10,
        "min_subtotal": 0.0,
        "max_discount": 0.0,
        "usage_limit": 0,
        "per_user_limit": 1,
        "starts_at": None,
        "expires_at": None,
        "is_active": True,
    },
    {
        "code": "PAWS20",
        "description": "20% off orders over $75, up to $25",
        "discount_type": "percent",
        "value": 20,
        "min_subtotal": 75.0,
        "max_discount": 25.0,
        "usage_limit": 0,
        "per_user_limit": 0,
        "starts_at": None,
        "expires_at": None,
        "is_active": True,
    },
    {
        "code": "FETCH5",
        "description": "$5 off orders over $30",
        "discount_type": "fixed",
        "value": 5.0,
        "min_subtotal": 30.0,
        "max_discount": 0.0,
        "usage_limit": 500,
        "per_user_limit": 2,
        "starts_at": None,
        "expires_at": None,
        "is_active": True,
    },
    {
        "code": "GROW15",
        "description": "15% off orders over $40 — plant launch offer",
        "discount_type": "percent",
        "value": 15,
        "min_subtotal": 40.0,
        "max_discount": 30.0,
        "usage_limit": 0,
        "per_user_limit": 1,
        "starts_at": None,
        "expires_at": None,
        "is_active": True,
    },
]

# (name, slug, description, category_slug, suitable_pet_types, brand, price, images, stock)
PRODUCTS = [
    ("Grain-Free Dry Dog Food (15lb)", "grain-free-dry-dog-food-15lb", "Chicken and sweet potato recipe.", "food", ["dog"], "NutriPaws", 39.99, 25),
    ("Wet Dog Food Variety Pack (12ct)", "wet-dog-food-variety-12ct", "Beef, chicken, and lamb recipes.", "food", ["dog"], "NutriPaws", 24.99, 30),
    ("Senior Dog Dry Food (15lb)", "senior-dog-dry-food-15lb", "Joint support formula for older dogs.", "food", ["dog"], "NutriPaws", 42.99, 20),
    ("Indoor Dry Cat Food (10lb)", "indoor-dry-cat-food-10lb", "Hairball control formula.", "food", ["cat"], "NutriPaws", 29.99, 28),
    ("Wet Cat Food Pate Variety (24ct)", "wet-cat-food-pate-24ct", "Grain-free pate in tuna and salmon.", "food", ["cat"], "NutriPaws", 27.99, 32),
    ("Timothy Hay Pellets (5lb)", "timothy-hay-pellets-5lb", "Staple diet food for rabbits and guinea pigs.", "food", ["rabbit"], "NutriPaws", 13.99, 20),
    ("Parrot Seed Mix (5lb)", "parrot-seed-mix-5lb", "Balanced seed blend for parrots and larger birds.", "food", ["bird"], "NutriPaws", 16.99, 18),
    ("Freeze-Dried Dog Treats", "freeze-dried-dog-treats", "Single-ingredient chicken breast treats.", "treats", ["dog"], "NutriPaws", 14.99, 45),
    ("Cat Dental Treats", "cat-dental-treats", "Helps reduce plaque and tartar buildup.", "treats", ["cat"], "NutriPaws", 6.49, 60),
    ("Rabbit Veggie Chews", "rabbit-veggie-chews", "Dried vegetable treats for rabbits.", "treats", ["rabbit"], "NutriPaws", 7.99, 25),
    ("Oatmeal Shampoo for Dogs", "oatmeal-shampoo-dogs", "Soothing oatmeal shampoo for sensitive skin.", "grooming", ["dog"], "PawClean", 12.99, 40),
    ("Hypoallergenic Cat Shampoo", "hypoallergenic-cat-shampoo", "Tearless formula safe for kittens.", "grooming", ["cat"], "PawClean", 11.49, 38),
    ("Slicker Brush", "slicker-brush", "Removes loose fur and prevents matting. Works for dogs and cats.", "grooming", ["dog", "cat"], "GroomPro", 9.99, 55),
    ("Nail Clippers", "nail-clippers", "Stainless steel clippers with safety guard.", "grooming", ["dog", "cat", "rabbit"], "GroomPro", 8.49, 60),
    ("Small Animal Grooming Brush", "small-animal-grooming-brush", "Soft bristle brush for rabbits and guinea pigs.", "grooming", ["rabbit"], "GroomPro", 5.99, 25),
    ("Rope Tug Toy", "rope-tug-toy", "Durable cotton rope for tug-of-war and chewing.", "toys", ["dog"], "PlayPaws", 8.99, 50),
    ("Feather Wand Toy", "feather-wand-toy", "Interactive feather wand for cats.", "toys", ["cat"], "PlayPaws", 6.99, 45),
    ("Bird Swing Perch", "bird-swing-perch", "Wooden swing perch for small to medium birds.", "toys", ["bird"], "PlayPaws", 10.99, 20),
    ("Chew Tunnel", "chew-tunnel", "Collapsible tunnel toy for rabbits and small pets.", "toys", ["rabbit"], "PlayPaws", 12.99, 18),
    ("Adjustable Dog Collar", "adjustable-dog-collar", "Durable nylon collar, adjustable sizing.", "accessories", ["dog"], "PawGear", 9.99, 50),
    ("Cat Harness & Leash Set", "cat-harness-leash-set", "Escape-resistant harness for outdoor walks.", "accessories", ["cat"], "PawGear", 15.99, 30),
    ("Stainless Steel Pet Bowl", "stainless-steel-pet-bowl", "Non-slip base, dishwasher safe. Fits most pets.", "accessories", ["dog", "cat"], "PawGear", 7.49, 70),
    ("Small Pet Carrier", "small-pet-carrier", "Ventilated travel carrier for rabbits and small pets.", "accessories", ["rabbit"], "PawGear", 24.99, 15),
]

# (name, slug, description, category_slug, brand, price, stock, plant_type,
#  light_needs, water_needs, care_level, mature_height_cm, pot_included, botanical_name)
PLANT_PRODUCTS = [
    ("Snake Plant", "snake-plant", "Near-indestructible upright foliage that tolerates low light and neglect.", "indoor-plants", "GreenNest", 24.99, 30, "indoor", "low", "low", "easy", 90, True, "Dracaena trifasciata"),
    ("ZZ Plant", "zz-plant", "Glossy dark leaves that thrive in dim corners and dry spells.", "indoor-plants", "GreenNest", 27.99, 24, "indoor", "low", "low", "easy", 75, True, "Zamioculcas zamiifolia"),
    ("Monstera Deliciosa", "monstera-deliciosa", "The classic split-leaf statement plant for bright rooms.", "indoor-plants", "GreenNest", 44.99, 18, "indoor", "bright_indirect", "medium", "moderate", 150, True, "Monstera deliciosa"),
    ("Peace Lily", "peace-lily", "Elegant white blooms and glossy leaves in medium light.", "indoor-plants", "GreenNest", 22.49, 26, "indoor", "medium", "medium", "easy", 60, True, "Spathiphyllum wallisii"),
    ("Pothos Golden", "pothos-golden", "Fast-growing trailing vine for shelves and hanging planters.", "indoor-plants", "GreenNest", 16.99, 40, "indoor", "medium", "medium", "easy", 200, True, "Epipremnum aureum"),
    ("Fiddle Leaf Fig", "fiddle-leaf-fig", "Large violin-shaped leaves — rewarding but particular about light.", "indoor-plants", "GreenNest", 59.99, 10, "indoor", "bright_indirect", "medium", "expert", 180, True, "Ficus lyrata"),
    ("Areca Palm", "areca-palm", "Feathery fronds that soften a room and love humidity.", "indoor-plants", "LeafLab", 39.99, 15, "indoor", "bright_indirect", "high", "moderate", 200, True, "Dypsis lutescens"),
    ("Rubber Plant Burgundy", "rubber-plant-burgundy", "Deep burgundy leaves with a thick, waxy finish.", "indoor-plants", "LeafLab", 34.99, 16, "indoor", "bright_indirect", "medium", "easy", 160, True, "Ficus elastica"),
    ("Bougainvillea", "bougainvillea", "Prolific papery blooms for a full-sun balcony or fence.", "outdoor-plants", "SunGarden", 29.99, 20, "outdoor", "full_sun", "medium", "moderate", 250, False, "Bougainvillea glabra"),
    ("Hibiscus Red", "hibiscus-red", "Big tropical blooms all through the warm months.", "outdoor-plants", "SunGarden", 26.99, 22, "outdoor", "full_sun", "high", "moderate", 180, False, "Hibiscus rosa-sinensis"),
    ("Lavender", "lavender", "Fragrant silver foliage and purple spikes that pollinators love.", "outdoor-plants", "SunGarden", 19.99, 28, "outdoor", "full_sun", "low", "easy", 60, False, "Lavandula angustifolia"),
    ("Jasmine Vine", "jasmine-vine", "Climbing vine with intensely scented evening flowers.", "outdoor-plants", "SunGarden", 24.99, 18, "outdoor", "full_sun", "medium", "moderate", 300, False, "Jasminum sambac"),
    ("Echeveria Rosette", "echeveria-rosette", "Compact pastel rosette succulent for a sunny windowsill.", "succulents-cacti", "TinyRoots", 12.99, 45, "succulent", "full_sun", "low", "easy", 15, True, "Echeveria elegans"),
    ("Jade Plant", "jade-plant", "Thick coin-shaped leaves on a slow-growing woody stem.", "succulents-cacti", "TinyRoots", 18.99, 32, "succulent", "full_sun", "low", "easy", 60, True, "Crassula ovata"),
    ("Aloe Vera", "aloe-vera", "Useful, architectural succulent with soothing leaf gel.", "succulents-cacti", "TinyRoots", 15.99, 38, "succulent", "full_sun", "low", "easy", 45, True, "Aloe barbadensis miller"),
    ("Assorted Cactus Trio", "assorted-cactus-trio", "Three small cacti in matching 3-inch terracotta pots.", "succulents-cacti", "TinyRoots", 21.99, 25, "succulent", "full_sun", "low", "easy", 20, True, "Cactaceae mix"),
    ("Basil Plant", "basil-plant", "Sweet Genovese basil, ready to pick for the kitchen.", "herbs-edibles", "HomeHarvest", 9.99, 50, "herb", "full_sun", "high", "easy", 45, True, "Ocimum basilicum"),
    ("Mint Plant", "mint-plant", "Vigorous spearmint — best kept in its own pot.", "herbs-edibles", "HomeHarvest", 8.99, 48, "herb", "medium", "high", "easy", 40, True, "Mentha spicata"),
    ("Rosemary Plant", "rosemary-plant", "Woody evergreen herb that prefers dry feet and full sun.", "herbs-edibles", "HomeHarvest", 11.99, 35, "herb", "full_sun", "low", "easy", 80, True, "Salvia rosmarinus"),
    ("Cherry Tomato Starter", "cherry-tomato-starter", "Young plant that fruits in about ten weeks.", "herbs-edibles", "HomeHarvest", 10.49, 30, "herb", "full_sun", "high", "moderate", 120, True, "Solanum lycopersicum"),
    ("Herb Garden Seed Kit", "herb-garden-seed-kit", "Six herb varieties with pellets, markers, and a guide.", "seeds-bulbs", "HomeHarvest", 22.99, 40, "seed", "full_sun", "medium", "easy", 0, False, ""),
    ("Wildflower Seed Mix (250g)", "wildflower-seed-mix-250g", "Pollinator-friendly annual and perennial blend.", "seeds-bulbs", "SunGarden", 14.99, 45, "seed", "full_sun", "medium", "easy", 0, False, ""),
    ("Tulip Bulbs (Pack of 20)", "tulip-bulbs-pack-20", "Mixed-colour spring bulbs for beds and containers.", "seeds-bulbs", "SunGarden", 18.99, 35, "seed", "full_sun", "medium", "easy", 0, False, "Tulipa gesneriana"),
    ("Microgreens Growing Tray", "microgreens-growing-tray", "Countertop tray and seed set for salad greens in 10 days.", "seeds-bulbs", "HomeHarvest", 26.99, 22, "seed", "medium", "high", "easy", 0, True, ""),
    ("Ceramic Planter 6in", "ceramic-planter-6in", "Matte glazed pot with drainage hole and saucer.", "planters-pots", "PotShed", 19.99, 40, "supply", "medium", "low", "easy", 0, False, ""),
    ("Terracotta Pot Set (3pc)", "terracotta-pot-set-3pc", "Classic breathable clay pots in 4, 6, and 8 inch sizes.", "planters-pots", "PotShed", 24.99, 30, "supply", "medium", "low", "easy", 0, False, ""),
    ("Macrame Hanging Planter", "macrame-hanging-planter", "Cotton rope hanger with a 6-inch ceramic pot.", "planters-pots", "PotShed", 27.49, 25, "supply", "medium", "low", "easy", 0, False, ""),
    ("Self-Watering Pot 8in", "self-watering-pot-8in", "Reservoir base that keeps soil evenly moist for two weeks.", "planters-pots", "PotShed", 32.99, 20, "supply", "medium", "low", "easy", 0, False, ""),
    ("Indoor Potting Mix (10L)", "indoor-potting-mix-10l", "Light, fast-draining blend for houseplants and herbs.", "soil-plant-care", "LeafLab", 14.99, 50, "supply", "medium", "medium", "easy", 0, False, ""),
    ("Cactus & Succulent Mix (5L)", "cactus-succulent-mix-5l", "Gritty mix that drains fast and prevents root rot.", "soil-plant-care", "LeafLab", 11.99, 45, "supply", "full_sun", "low", "easy", 0, False, ""),
    ("All-Purpose Liquid Fertiliser", "all-purpose-liquid-fertiliser", "Balanced NPK feed — one capful per litre, fortnightly.", "soil-plant-care", "LeafLab", 13.49, 55, "supply", "medium", "medium", "easy", 0, False, ""),
    ("Pruning Shears", "pruning-shears", "Stainless steel bypass shears with a locking latch.", "soil-plant-care", "PotShed", 16.99, 40, "supply", "medium", "low", "easy", 0, False, ""),
    ("Watering Can 1.5L", "watering-can-1-5l", "Long-spout can for reaching the base of dense foliage.", "soil-plant-care", "PotShed", 18.49, 35, "supply", "medium", "medium", "easy", 0, False, ""),
    ("Neem Oil Spray", "neem-oil-spray", "Ready-to-use spray for aphids, mites, and fungus gnats.", "soil-plant-care", "LeafLab", 12.99, 42, "supply", "medium", "low", "easy", 0, False, ""),
]


async def main():
    if mongodb.database is None:
        print("MONGODB_URI is empty in backend/.env — set it to a real connection string before seeding.")
        sys.exit(1)

    db = mongodb.database

    admin = await db.users.find_one({"email": ADMIN_EMAIL})
    if not admin:
        await db.users.insert_one(
            {
                "name": "Admin",
                "email": ADMIN_EMAIL,
                "hashed_password": hash_password(ADMIN_PASSWORD),
                "is_admin": True,
                "is_active": True,
                "addresses": [],
            }
        )
        print(f"Created admin user: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")
    else:
        print("Admin user already exists, skipping.")

    shopper = await db.users.find_one({"email": SHOPPER_EMAIL})
    if not shopper:
        result = await db.users.insert_one(
            {
                "name": "Sam Shopper",
                "email": SHOPPER_EMAIL,
                "hashed_password": hash_password(SHOPPER_PASSWORD),
                "is_admin": False,
                "is_active": True,
                "addresses": [],
            }
        )
        shopper_id = str(result.inserted_id)
        print(f"Created demo customer: {SHOPPER_EMAIL} / {SHOPPER_PASSWORD}")
    else:
        shopper_id = str(shopper["_id"])
        print("Demo customer already exists, skipping.")

    pets_inserted = 0
    for pet in SHOPPER_PETS:
        if await db.pets.find_one({"owner_id": shopper_id, "name": pet["name"]}):
            continue
        await db.pets.insert_one({**pet, "owner_id": shopper_id})
        pets_inserted += 1
    if pets_inserted:
        # A puppy and a senior cat, so the recommender's life-stage scoring has
        # something to actually discriminate between.
        print(f"Demo pets created: {pets_inserted}")

    # Databases seeded before the plant catalogue existed have documents with no
    # kind at all. Stamp them as pet so the admin lists and facets stay accurate.
    backfilled_categories = await db.categories.update_many({"kind": {"$exists": False}}, {"$set": {"kind": "pet"}})
    backfilled_products = await db.products.update_many(
        {"product_kind": {"$exists": False}}, {"$set": {"product_kind": "pet", "plant": None}}
    )
    if backfilled_categories.modified_count or backfilled_products.modified_count:
        print(
            f"Backfilled kind on {backfilled_categories.modified_count} categories "
            f"and {backfilled_products.modified_count} products."
        )

    slug_to_id: dict[str, str] = {}
    for category in CATEGORIES:
        existing = await db.categories.find_one({"slug": category["slug"]})
        if existing:
            slug_to_id[category["slug"]] = str(existing["_id"])
            continue
        result = await db.categories.insert_one(dict(category))
        slug_to_id[category["slug"]] = str(result.inserted_id)
    print(f"Categories ready: {len(slug_to_id)}")

    def category_name(category_slug: str) -> str:
        return next(c["name"] for c in CATEGORIES if c["slug"] == category_slug)

    def gallery(name: str) -> list[str]:
        """Three stand-in shots so the product gallery has something to switch
        between. Swap these for real photography via the admin product form."""
        text = name.replace(" ", "+")
        return [
            f"https://placehold.co/600x600.png?text={text}",
            f"https://placehold.co/600x600/e7e5e4/44403c.png?text={text}+detail",
            f"https://placehold.co/600x600/f5f5f4/57534e.png?text={text}+in+use",
        ]

    inserted, skipped = 0, 0
    for name, slug, description, category_slug, pet_types, brand, price, stock in PRODUCTS:
        existing = await db.products.find_one({"slug": slug})
        if existing:
            skipped += 1
            continue
        await db.products.insert_one(
            {
                "name": name,
                "slug": slug,
                "description": description,
                "category_id": slug_to_id[category_slug],
                "category_name": category_name(category_slug),
                "product_kind": "pet",
                "suitable_pet_types": pet_types,
                "plant": None,
                "brand": brand,
                "price": price,
                "compare_at_price": SALE_PRICES.get(slug, 0),
                "images": gallery(name),
                "stock": stock,
                "rating": 0.0,
                "rating_count": 0,
            }
        )
        inserted += 1

    print(f"Pet products inserted: {inserted}, skipped (already existed): {skipped}")

    plants_inserted, plants_skipped = 0, 0
    for (
        name,
        slug,
        description,
        category_slug,
        brand,
        price,
        stock,
        plant_type,
        light_needs,
        water_needs,
        care_level,
        mature_height_cm,
        pot_included,
        botanical_name,
    ) in PLANT_PRODUCTS:
        existing = await db.products.find_one({"slug": slug})
        if existing:
            plants_skipped += 1
            continue
        await db.products.insert_one(
            {
                "name": name,
                "slug": slug,
                "description": description,
                "category_id": slug_to_id[category_slug],
                "category_name": category_name(category_slug),
                "product_kind": "plant",
                "suitable_pet_types": [],
                "plant": {
                    "plant_type": plant_type,
                    "light_needs": light_needs,
                    "water_needs": water_needs,
                    "care_level": care_level,
                    "mature_height_cm": mature_height_cm,
                    "pot_included": pot_included,
                    "botanical_name": botanical_name,
                },
                "brand": brand,
                "price": price,
                "compare_at_price": SALE_PRICES.get(slug, 0),
                "images": gallery(name),
                "stock": stock,
                "rating": 0.0,
                "rating_count": 0,
            }
        )
        plants_inserted += 1

    print(f"Plant products inserted: {plants_inserted}, skipped (already existed): {plants_skipped}")

    # Sales and galleries are applied to products that already existed too, so
    # re-running the seed on a database from an earlier version lights both up.
    sales_applied = 0
    for slug, was_price in SALE_PRICES.items():
        result = await db.products.update_one(
            {"slug": slug, "price": {"$lt": was_price}}, {"$set": {"compare_at_price": was_price}}
        )
        sales_applied += result.modified_count
    # Everything else is explicitly full price, so the "On sale" facet counts are honest.
    await db.products.update_many(
        {"compare_at_price": {"$exists": False}}, {"$set": {"compare_at_price": 0}}
    )
    print(f"Sale prices applied: {sales_applied}")

    galleries_backfilled = 0
    async for doc in db.products.find({"images": {"$size": 1}}):
        # Only ever replaces our own placeholders — a real photo added through
        # the admin form is left exactly as it is.
        if "placehold.co" not in (doc.get("images") or [""])[0]:
            continue
        await db.products.update_one({"_id": doc["_id"]}, {"$set": {"images": gallery(doc["name"])}})
        galleries_backfilled += 1
    if galleries_backfilled:
        print(f"Product galleries expanded: {galleries_backfilled}")

    questions_inserted = 0
    for product_slug, body, answer_body in QUESTIONS:
        product = await db.products.find_one({"slug": product_slug})
        if not product or await db.questions.find_one({"product_id": str(product["_id"]), "body": body}):
            continue
        now = datetime.now(timezone.utc)
        await db.questions.insert_one(
            {
                "product_id": str(product["_id"]),
                "product_name": product["name"],
                "product_slug": product["slug"],
                "user_id": shopper_id,
                "user_name": "Sam Shopper",
                "body": body,
                "created_at": now,
                "answer": (
                    {"body": answer_body, "answered_by": "Admin", "answered_at": now} if answer_body else None
                ),
            }
        )
        questions_inserted += 1
    unanswered = sum(1 for _, _, answer in QUESTIONS if answer is None)
    print(f"Product questions inserted: {questions_inserted} ({unanswered} left unanswered for /admin/questions)")

    coupons_inserted = 0
    for coupon in COUPONS:
        if await db.coupons.find_one({"code": coupon["code"]}):
            continue
        await db.coupons.insert_one({**coupon, "used_count": 0})
        coupons_inserted += 1
    print(f"Coupons inserted: {coupons_inserted} (codes: {', '.join(c['code'] for c in COUPONS)})")


if __name__ == "__main__":
    asyncio.run(main())
