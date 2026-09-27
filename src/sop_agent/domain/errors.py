"""Domain-specific exceptions."""


class DomainError(Exception):
    """Base class for errors raised by the domain and data layers."""


class AmountParseError(DomainError):
    """A monetary value could not be parsed as a decimal amount."""


class DataLoadError(DomainError):
    """Fixture data is structurally invalid; raised at startup so bad data never reaches a session."""

    def __init__(self, file: str, record: str, reason: str) -> None:
        self.file = file
        self.record = record
        self.reason = reason
        super().__init__(f"{file} [{record}]: {reason}")
