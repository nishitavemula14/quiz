from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import hash_password, verify_password
from app.models import (
    Attempt,
    AttemptAnswer,
    AttemptQuestionResult,
    AttemptStatus,
    Question,
    QuestionOption,
    Quiz,
    User,
    UserRole,
)
from app.schemas import QuizCreate, OPTION_KEYS
from app.time_utils import utc_now


def create_user(db: Session, username: str, password: str, role: UserRole) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return user


def create_quiz(db: Session, payload: QuizCreate, creator: User) -> Quiz:
    quiz = Quiz(
        creator_id=creator.id,
        title=payload.title,
        description=payload.description,
        time_limit_minutes=payload.time_limit_minutes,
    )
    db.add(quiz)
    db.flush()

    for question_in in payload.questions:
        question = Question(quiz_id=quiz.id, prompt=question_in.prompt)
        db.add(question)
        db.flush()
        for index, option_text in enumerate(question_in.options):
            option_key = OPTION_KEYS[index]
            db.add(
                QuestionOption(
                    question_id=question.id,
                    option_key=option_key,
                    option_text=option_text,
                    is_correct=option_key == question_in.correct_option,
                )
            )

    db.commit()
    db.refresh(quiz)
    return quiz


def load_quiz_for_attempt(db: Session, quiz_id: int) -> Quiz:
    quiz = db.scalar(
        select(Quiz)
        .where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.questions).selectinload(Question.options))
    )
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")
    return quiz


def start_attempt(db: Session, quiz: Quiz, user: User) -> Attempt:
    if quiz.creator_id == user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Teachers cannot take their own quizzes")

    active = db.scalar(
        select(Attempt).where(
            Attempt.quiz_id == quiz.id,
            Attempt.user_id == user.id,
            Attempt.status == AttemptStatus.active,
        )
    )
    if active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Active attempt already exists")

    attempt = Attempt(
        quiz_id=quiz.id,
        user_id=user.id,
        status=AttemptStatus.active,
        started_at=utc_now(),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def load_attempt(db: Session, attempt_id: int) -> Attempt:
    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.id == attempt_id)
        .options(
            selectinload(Attempt.quiz).selectinload(Quiz.questions).selectinload(Question.options),
            selectinload(Attempt.answers),
            selectinload(Attempt.question_results),
            selectinload(Attempt.user),
        )
    )
    if not attempt:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attempt not found")
    return attempt


def ensure_attempt_owner_or_teacher(attempt: Attempt, current_user: User) -> None:
    if current_user.role == UserRole.student and attempt.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    if current_user.role == UserRole.teacher and attempt.quiz.creator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")


def _score_attempt_in_place(db: Session, attempt: Attempt, expired: bool = False) -> Attempt:
    answer_by_question = {answer.question_id: answer.chosen_option_key for answer in attempt.answers}
    db.query(AttemptQuestionResult).filter(AttemptQuestionResult.attempt_id == attempt.id).delete()

    correct_count = 0
    for question in attempt.quiz.questions:
        chosen = answer_by_question.get(question.id)
        correct_option = next(option.option_key for option in question.options if option.is_correct)
        is_correct = chosen == correct_option
        if is_correct:
            correct_count += 1
        db.add(
            AttemptQuestionResult(
                attempt_id=attempt.id,
                question_id=question.id,
                chosen_option_key=chosen,
                is_correct=is_correct,
            )
        )

    total = len(attempt.quiz.questions)
    attempt.score = int((correct_count / total) * 100) if total else 0
    attempt.status = AttemptStatus.expired if expired else AttemptStatus.finished
    attempt.finished_at = utc_now()
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return load_attempt(db, attempt.id)


def enforce_not_expired(db: Session, attempt: Attempt) -> Attempt:
    if attempt.status != AttemptStatus.active:
        return attempt

    expires_at = attempt.started_at + timedelta(minutes=attempt.quiz.time_limit_minutes)
    if utc_now() > expires_at:
        return _score_attempt_in_place(db, attempt, expired=True)
    return attempt


def submit_answer(db: Session, attempt: Attempt, question_id: int, chosen_option: str) -> AttemptAnswer:
    if attempt.status != AttemptStatus.active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Attempt is not active")

    question = next((item for item in attempt.quiz.questions if item.id == question_id), None)
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found in this quiz")

    if chosen_option not in {option.option_key for option in question.options}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid option for question")

    answer = db.scalar(
        select(AttemptAnswer).where(
            AttemptAnswer.attempt_id == attempt.id,
            AttemptAnswer.question_id == question_id,
        )
    )
    if answer:
        answer.chosen_option_key = chosen_option
    else:
        answer = AttemptAnswer(
            attempt_id=attempt.id,
            question_id=question_id,
            chosen_option_key=chosen_option,
        )
        db.add(answer)

    db.commit()
    db.refresh(answer)
    return answer


def finish_attempt(db: Session, attempt: Attempt) -> Attempt:
    if attempt.status != AttemptStatus.active:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Attempt is not active")
    return _score_attempt_in_place(db, attempt, expired=False)
