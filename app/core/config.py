from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongodb_uri: str = ""
    db_name: str = "petstore"

    jwt_secret: str = "dev-only-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    cors_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    # Session cookie attributes. The defaults suit a same-site setup — local
    # http, or a frontend that proxies this API under its own origin. Set
    # COOKIE_SECURE=true over https. Only set COOKIE_SAMESITE=none (which
    # browsers accept solely alongside Secure) if the frontend calls this API
    # cross-site, and note that Safari and Brave block such cookies outright.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # Order pricing. Shipping is waived once the discounted subtotal reaches the
    # threshold; tax is applied after any coupon discount.
    shipping_flat_fee: float = 5.99
    free_shipping_threshold: float = 49.0
    tax_rate: float = 0.08

    # Password reset links expire quickly — they grant account access.
    reset_token_expire_minutes: int = 60

    # How long after delivery a customer may open a return. Staff can still
    # refund an older order by hand; this only bounds the self-service window.
    return_window_days: int = 30

    # Only someone who bought the product and had it delivered may review it.
    # Turning this off restores the old behaviour of letting anyone rate anything.
    require_verified_purchase: bool = True

    # Leave smtp_host blank to run without a mail server: emails are logged to
    # the console instead of being sent, mirroring the payment-gateway fallback.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from: str = "no-reply@furryfriends.test"

    # ------------------------------------------------------------------ #
    # Loyalty
    # ------------------------------------------------------------------ #
    # Points are earned on what the customer actually paid for goods (after any
    # discount, before tax and shipping) and only once the order is delivered.
    # The two rates together set the effective return: at 10 earned per unit of
    # currency and 200 needed to redeem one, a customer gets 5% back.
    loyalty_enabled: bool = True
    loyalty_points_per_currency: float = 10.0
    loyalty_points_per_redeemed_currency: float = 200.0
    # Redeeming a handful of points is more friction than it is worth to either
    # side, and paying for an entire order in points would refund shipping and
    # tax out of the store's pocket.
    loyalty_min_redemption: int = 200
    loyalty_max_redemption_percent: float = 0.5
    # Lifetime points needed for each tier, and the earn multiplier it buys.
    loyalty_silver_threshold: int = 2500
    loyalty_gold_threshold: int = 10000
    loyalty_platinum_threshold: int = 25000

    # ------------------------------------------------------------------ #
    # Referrals
    # ------------------------------------------------------------------ #
    # Both sides are paid when the newcomer's first order is *delivered*, not
    # when they sign up — an invite that never buys anything costs nothing.
    referrals_enabled: bool = True
    referral_referrer_points: int = 1000
    referral_referee_points: int = 500

    # ------------------------------------------------------------------ #
    # Subscriptions
    # ------------------------------------------------------------------ #
    # Repeat delivery of the things that run out. The discount is the trade for
    # the commitment, and applies to every order the subscription places.
    subscriptions_enabled: bool = True
    subscription_discount_percent: float = 10.0
    subscription_min_interval_days: int = 7
    subscription_max_interval_days: int = 180
    # A due subscription whose product is out of stock is retried on the next
    # run rather than failed outright; after this many consecutive misses it is
    # paused and the customer is told, so it can't retry forever in silence.
    subscription_max_failures: int = 3

    # The in-process scheduler that actually places due deliveries. Without it
    # `run_due` only ever fires when an admin presses the button, so in a
    # deployed store no repeat delivery would be placed at all.
    #
    # Turn this off if you would rather drive POST /admin/subscriptions/run from
    # a real scheduler — running both at once is safe, since due rows are claimed
    # before they are acted on.
    subscription_runner_enabled: bool = True
    subscription_runner_interval_minutes: int = 60
    subscription_runner_batch_limit: int = 100
    # Nothing useful happens in the first seconds of a boot, and staggering the
    # first pass keeps a redeploy of several instances from all starting at once.
    subscription_runner_initial_delay_seconds: int = 30

    # The store is also exposed as an MCP server at /mcp so agents can browse the
    # catalogue, manage a cart, and (with an admin token) run the shop. Tools are
    # scoped by whatever credential the caller presents, exactly like the REST API.
    mcp_enabled: bool = True
    # Hosts the MCP endpoint will answer to. Streamable HTTP refuses unknown Host
    # headers to block DNS rebinding, so a deployed API must list its own domain.
    mcp_allowed_hosts: str = "127.0.0.1:*,localhost:*,[::1]:*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def mcp_allowed_host_list(self) -> list[str]:
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host)


settings = Settings()
