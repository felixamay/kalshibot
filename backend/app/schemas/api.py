"""Pydantic schemas for API I/O."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    display_name: Optional[str] = None


class ManualEntryRequest(BaseModel):
    signal_id: Optional[str] = None
    market_ticker: str
    direction: str = "YES"
    player: str
    entry_price: float = Field(ge=1, le=99)
    amount: float = Field(gt=0)
    notes: Optional[str] = None


class ManualExitRequest(BaseModel):
    position_id: str
    exit_price: float = Field(ge=1, le=99)
    amount: float = Field(gt=0)
    notes: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    server_time_ms: float
    connection_status: str
    live_matches: int
    database: str
    kalshi_read_only: bool = True
    order_placement_enabled: bool = False
