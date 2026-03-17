import uuid
from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.constants import GenderType, UserRole
from app.core.validators import validate_international_phone
from app.schemas.common import BaseSchema


class UserResponse(BaseSchema):
    id: uuid.UUID
    email: str | None
    phone: str | None
    first_name: str | None
    last_name: str | None
    role: UserRole
    avatar_url: str | None
    date_of_birth: date | None
    gender: GenderType | None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    email_verified: bool
    phone_verified: bool
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserUpdateRequest(BaseModel):
    first_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=20)
    date_of_birth: date | None = None
    gender: GenderType | None = None
    avatar_url: str | None = Field(None, max_length=500)
    emergency_contact_name: str | None = Field(None, max_length=200)
    emergency_contact_phone: str | None = Field(None, max_length=20)

    @field_validator("phone", "emergency_contact_phone")
    @classmethod
    def check_phone(cls, v: str | None) -> str | None:
        return validate_international_phone(v)


class AdminUserUpdateRequest(UserUpdateRequest):
    role: UserRole | None = None
    is_active: bool | None = None
    email_verified: bool | None = None
    phone_verified: bool | None = None
