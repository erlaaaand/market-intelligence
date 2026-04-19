# src/core/exceptions.py

"""
Custom domain exceptions for the agent_market_intelligence module.

Following DDD principles, exceptions are defined in the core layer
and represent business-meaningful error conditions.
"""


class AgentMarketIntelligenceError(Exception):
    """Base exception for the agent_market_intelligence module."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)


class DataExtractionError(AgentMarketIntelligenceError):
    """
    Raised when data cannot be extracted from an external source.

    This may be due to network issues, malformed responses,
    or unexpected data formats from the provider.
    """

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(
            f"Failed to extract data from '{source}': {reason}"
        )


class RateLimitExceededError(AgentMarketIntelligenceError):
    """
    Raised when an external API returns a rate-limit error (HTTP 429).

    Callers should implement exponential backoff or retry logic
    upon catching this exception.
    """

    def __init__(self, source: str, retry_after_seconds: int | None = None) -> None:
        self.source = source
        self.retry_after_seconds = retry_after_seconds
        hint = (
            f" Retry after {retry_after_seconds}s."
            if retry_after_seconds is not None
            else " Consider adding a delay before retrying."
        )
        super().__init__(
            f"Rate limit exceeded for '{source}'.{hint}"
        )


class StorageError(AgentMarketIntelligenceError):
    """
    Raised when data cannot be persisted to the storage backend.
    """

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(
            f"Storage operation failed at '{path}': {reason}"
        )
        