class DomainError(Exception):
    """Base class for all business/domain errors raised by the service layer."""


class NotFoundError(DomainError):
    def __init__(self, message: str = "Resource not found"):
        self.message = message
        super().__init__(message)


class ConflictError(DomainError):
    def __init__(self, message: str = "Resource already exists"):
        self.message = message
        super().__init__(message)


class ForbiddenError(DomainError):
    def __init__(self, message: str = "Not allowed to perform this action"):
        self.message = message
        super().__init__(message)


class ValidationError(DomainError):
    def __init__(self, message: str = "Invalid request"):
        self.message = message
        super().__init__(message)


class UnauthorizedError(DomainError):
    def __init__(self, message: str = "Not authenticated"):
        self.message = message
        super().__init__(message)


class InsufficientStockError(DomainError):
    def __init__(self, message: str = "Not enough stock available"):
        self.message = message
        super().__init__(message)


class PaymentGatewayNotConfiguredError(DomainError):
    def __init__(self, message: str = "Payment gateway is not configured"):
        self.message = message
        super().__init__(message)


class DatabaseNotConfiguredError(DomainError):
    def __init__(self, message: str = "Database is not configured (MONGODB_URI is empty)"):
        self.message = message
        super().__init__(message)
