from __future__ import annotations


class ExternalAPIError(Exception):
    """Raised when external LLM/Embedding API is unavailable.

    Covers quota exhaustion, auth failures, server errors, and network issues.
    Used to signal that ingestion should be aborted without retry.
    """

    pass
