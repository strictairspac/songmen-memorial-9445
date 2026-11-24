from typing import Any

from pydantic import BaseModel, Field


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CaseForm(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class ReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class CorrectionForm(BaseModel):
    basis_evidence_id: int = Field(gt=0)
    month: str = Field(min_length=7, max_length=10)
    corrected_state: str = Field(min_length=1, max_length=20)


class SupplementReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)
