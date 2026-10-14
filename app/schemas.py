"""Request / response models for the API."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = ""
    route: Optional[str] = None
    sql: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[HistoryItem] = Field(default_factory=list)
    route: Optional[Literal["analytics", "knowledge", "general"]] = Field(
        default=None, description="Force a route (skip the router). Leave empty for automatic routing.")


class SQLRequest(BaseModel):
    sql: str = Field(..., min_length=6, max_length=5000)
    limit: int = Field(default=200, ge=1, le=5000)
