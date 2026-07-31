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
