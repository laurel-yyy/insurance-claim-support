"""Request/response DTOs for the HTTP API."""

from datetime import date

from pydantic import BaseModel

from sop_agent.data.loaders import DataSummary


class HealthResponse(BaseModel):
    """Health check payload. Reports whether a key is configured, never the key itself (INV-9)."""

    status: str
    llm_configured: bool
    demo_today: date
    data_summary: DataSummary
