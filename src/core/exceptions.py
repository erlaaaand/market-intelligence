from __future__ import annotations


class AgentMarketIntelligenceError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(self.message)


class DataExtractionError(AgentMarketIntelligenceError):
    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"Failed to extract data from '{source}': {reason}")


class RateLimitExceededError(AgentMarketIntelligenceError):
    def __init__(self, source: str, retry_after_seconds: int | None = None) -> None:
        self.source = source
        self.retry_after_seconds = retry_after_seconds
        hint = (
            f" Retry after {retry_after_seconds}s."
            if retry_after_seconds is not None
            else " Consider adding a delay before retrying."
        )
        super().__init__(f"Rate limit exceeded for '{source}'.{hint}")


class StorageError(AgentMarketIntelligenceError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Storage operation failed at '{path}': {reason}")


class LLMAnalysisError(AgentMarketIntelligenceError):
    def __init__(self, model: str, reason: str) -> None:
        self.model = model
        self.reason = reason
        super().__init__(f"LLM analysis failed (model='{model}'): {reason}")


class TrendFileNotFoundError(AgentMarketIntelligenceError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"No trend file found at '{path}': {reason}")


class TrendFileParseError(AgentMarketIntelligenceError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to parse trend file at '{path}': {reason}")


class BriefGenerationError(AgentMarketIntelligenceError):
    def __init__(self, topic: str, reason: str) -> None:
        self.topic = topic
        self.reason = reason
        super().__init__(f"Failed to generate brief for topic '{topic}': {reason}")