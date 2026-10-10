"""Stable, JSON-serializable errors; retrieval failures are never empty success."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceError(Exception):
    code: str
    message: str
    url: str = ""
    status_code: int | None = None
    retry_after_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "source_limited", "ok": False,
            "error": {"code": self.code, "message": self.message, "url": self.url,
                      "status_code": self.status_code,
                      "retry_after_seconds": self.retry_after_seconds, **self.details},
            "coverage_complete": False,
        }
