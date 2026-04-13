from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(str, Enum):
    teacher = "teacher"
    student = "student"


class AttemptStatus(str, Enum):
    active = "active"
    finished = "finished"
    expired = "expired"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), nullable=False)

    quizzes: Mapped[list[Quiz]] = relationship(back_populates="creator")
    attempts: Mapped[list[Attempt]] = relationship(back_populates="user")


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    time_limit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    creator: Mapped[User] = relationship(back_populates="quizzes")
    questions: Mapped[list[Question]] = relationship(back_populates="quiz", cascade="all, delete-orphan")
    attempts: Mapped[list[Attempt]] = relationship(back_populates="quiz")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text(), nullable=False)

    quiz: Mapped[Quiz] = relationship(back_populates="questions")
    options: Mapped[list[QuestionOption]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )
    answers: Mapped[list[AttemptAnswer]] = relationship(back_populates="question")
    results: Mapped[list[AttemptQuestionResult]] = relationship(back_populates="question")


class QuestionOption(Base):
    __tablename__ = "question_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    option_key: Mapped[str] = mapped_column(String(1), nullable=False)
    option_text: Mapped[str] = mapped_column(Text(), nullable=False)
    is_correct: Mapped[bool] = mapped_column(nullable=False, default=False)

    question: Mapped[Question] = relationship(back_populates="options")


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[AttemptStatus] = mapped_column(SqlEnum(AttemptStatus), nullable=False, default=AttemptStatus.active)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")
    user: Mapped[User] = relationship(back_populates="attempts")
    answers: Mapped[list[AttemptAnswer]] = relationship(back_populates="attempt", cascade="all, delete-orphan")
    question_results: Mapped[list[AttemptQuestionResult]] = relationship(
        back_populates="attempt",
        cascade="all, delete-orphan",
    )


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question_answer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    chosen_option_key: Mapped[str] = mapped_column(String(1), nullable=False)

    attempt: Mapped[Attempt] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="answers")


class AttemptQuestionResult(Base):
    __tablename__ = "attempt_question_results"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_attempt_question_result"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False, index=True)
    chosen_option_key: Mapped[str | None] = mapped_column(String(1), nullable=True)
    is_correct: Mapped[bool] = mapped_column(nullable=False)

    attempt: Mapped[Attempt] = relationship(back_populates="question_results")
    question: Mapped[Question] = relationship(back_populates="results")
