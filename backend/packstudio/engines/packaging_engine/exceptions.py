"""Domain exceptions with stable machine-readable codes."""

from __future__ import annotations


class PackagingError(Exception):
    """Base class for all packaging-domain errors."""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class RequestValidationError(PackagingError):
    """The inbound request is structurally invalid or missing fields."""


class StructureNotImplementedError(PackagingError):
    """The requested box type has no deterministic implementation."""


class ValidationFailedError(PackagingError):
    """A dieline failed a mechanical/structural validation gate."""


class ExportError(PackagingError):
    """A file export failed."""


class ProviderError(PackagingError):
    """A CMF/visual provider is missing, misconfigured, or returned an error."""


class KnowledgeMissingError(PackagingError):
    """A required knowledge-base entry is absent."""
