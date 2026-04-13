from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models import AttemptStatus, UserRole


OPTION_KEYS = ["A", "B", "C", "D"]


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=8)
    role: UserRole


class UserResponse(BaseModel):
    id: int
    username: str
    role: UserRole


class QuestionCreate(BaseModel):
    prompt: str
    options: list[str] = Field(min_length=4, max_length=4)
    correct_option: str

    @field_validator("correct_option")
    @classmethod
    def validate_correct_option(cls, value: str) -> str:
        upper = value.upper()
        if upper not in OPTION_KEYS:
            raise ValueError("correct_option must be one of A, B, C, D")
        return upper


class QuizCreate(BaseModel):
    title: str
    description: str
    time_limit_minutes: int = Field(gt=0)
    questions: list[QuestionCreate] = Field(min_length=1)


class QuizCreatedResponse(BaseModel):
    id: int
    title: str
    description: str
    time_limit_minutes: int


class QuestionPublic(BaseModel):
    id: int
    prompt: str
    options: list[str]


class AttemptStartResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    started_at: datetime
    status: AttemptStatus
    questions: list[QuestionPublic]


class AnswerSubmitRequest(BaseModel):
    question_id: int
    chosen_option: str

    @field_validator("chosen_option")
    @classmethod
    def validate_choice(cls, value: str) -> str:
        upper = value.upper()
        if upper not in OPTION_KEYS:
            raise ValueError("chosen_option must be one of A, B, C, D")
        return upper


class FinishAttemptResponse(BaseModel):
    attempt_id: int
    status: AttemptStatus
    score: int
    time_taken_seconds: int


class AttemptBreakdownItem(BaseModel):
    question_id: int
    chosen_option: str | None
    is_correct: bool | None


class AttemptResultResponse(BaseModel):
    attempt_id: int
    quiz_id: int
    status: AttemptStatus
    score: int | None
    time_taken_seconds: int | None
    breakdown: list[AttemptBreakdownItem]


class QuizAttemptSummary(BaseModel):
    attempt_id: int
    username: str
    score: int | None
    time_taken_seconds: int | None
    status: AttemptStatus
