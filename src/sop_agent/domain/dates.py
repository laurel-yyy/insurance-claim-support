"""Date hints stated by the caller. Parsing into ranges and deadline status arrive with the resolver (M2)."""

from pydantic import BaseModel, ConfigDict


class DateHint(BaseModel):
    """A partial date the caller mentioned ("January", "last March", "2026-01-12"); each part is optional."""

    model_config = ConfigDict(frozen=True)

    year: int | None = None
    month: int | None = None
    day: int | None = None

    def is_empty(self) -> bool:
        return self.year is None and self.month is None and self.day is None
