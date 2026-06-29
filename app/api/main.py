"""FastAPI application surface.

Step 0 exposes only a health check. Pipeline endpoints are added in later steps.
Run with: ``uvicorn app.api.main:app``.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Multi-Source Candidate Data Transformer",
    version="0.1.0",
)


class HealthResponse(BaseModel):
    """Response model for the health check endpoint."""

    status: Literal["ok"]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report service liveness."""
    return HealthResponse(status="ok")
