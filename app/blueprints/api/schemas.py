# app/blueprints/api/schemas.py

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Literal
from datetime import datetime

class CreateInviteSchema(BaseModel):
    libraries: List[str] = Field(..., min_items=1, description="Pelo menos uma biblioteca deve ser selecionada.")
    screens: int = Field(0, ge=0, le=4)
    allow_downloads: bool = False
    expires_in_minutes: Optional[int] = Field(None, ge=0)
    trial_duration_minutes: int = Field(0, ge=0)
    overseerr_access: bool = False
    custom_code: Optional[str] = None
    max_uses: int = Field(1, ge=1)

class RenewSubscriptionSchema(BaseModel):
    months: int = Field(..., gt=0)
    base: Literal['today', 'expiry_date'] = 'today'
    base_date: Optional[str] = None
    expiration_time: Optional[str] = None

    @validator('base_date')
    def validate_base_date(cls, v):
        if v is None:
            return v
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            raise ValueError("O formato da data base deve ser YYYY-MM-DD")

    @validator('expiration_time')
    def validate_expiration_time(cls, v):
        if v is None:
            return v
        try:
            datetime.strptime(v, '%H:%M')
            return v
        except ValueError:
            raise ValueError("O formato da hora de expiração deve ser HH:MM")

class UpdateProfileSchema(BaseModel):
    name: Optional[str] = None
    telegram_user: Optional[str] = None
    discord_user_id: Optional[str] = None
    phone_number: Optional[str] = None
    expiration_datetime_local: Optional[str] = None
    
    @validator('expiration_datetime_local')
    def validate_expiration_datetime(cls, v):
        if v is None:
            return v
        try:
            # Tenta analisar o formato esperado (YYYY-MM-DDTHH:MM)
            datetime.fromisoformat(v)
            return v
        except (ValueError, TypeError):
            raise ValueError("Formato de data/hora de expiração inválido.")

class UpdateAccountProfileSchema(BaseModel):
    name: Optional[str] = None
    telegram_user: Optional[str] = None
    discord_user_id: Optional[str] = None
    phone_number: Optional[str] = None
