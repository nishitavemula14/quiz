from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, require_role
from app.database import get_db
from app.models import Attempt, AttemptStatus, Quiz, User, UserRole
from app.schemas import (
    AnswerSubmitRequest,
    AttemptBreakdownItem,
    AttemptResultResponse,
    AttemptStartResponse,
    FinishAttemptResponse,
    QuestionPublic,
    QuizAttemptSummary,
    QuizCreate,
    QuizCreatedResponse,
    TokenRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services import (
    authenticate_user,
    create_user,
    create_quiz,
    enforce_not_expired,
    ensure_attempt_owner_or_teacher,
    finish_attempt,
    load_attempt,
    load_quiz_for_attempt,
    start_attempt,
    submit_answer,
)

app = FastAPI(title="Online Quiz & Exam Platform")


@app.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(payload: UserCreate, db: Session = Depends(get_db)) -> UserResponse:
    user = create_user(db, payload.username, payload.password, payload.role)
    return UserResponse.model_validate(user, from_attributes=True)


@app.post("/auth/token", response_model=TokenResponse)
def issue_token(payload: TokenRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = authenticate_user(db, payload.username, payload.password)
    return TokenResponse(access_token=create_access_token(user))


@app.post("/quizzes", response_model=QuizCreatedResponse)
def create_quiz_endpoint(
    payload: QuizCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.teacher)),
) -> QuizCreatedResponse:
    quiz = create_quiz(db, payload, current_user)
    return QuizCreatedResponse.model_validate(quiz, from_attributes=True)


@app.post("/quizzes/{quiz_id}/attempts", response_model=AttemptStartResponse)
def start_attempt_endpoint(
    quiz_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.student)),
) -> AttemptStartResponse:
    quiz = load_quiz_for_attempt(db, quiz_id)
    attempt = start_attempt(db, quiz, current_user)
    return AttemptStartResponse(
        attempt_id=attempt.id,
        quiz_id=attempt.quiz_id,
        started_at=attempt.started_at,
        status=attempt.status,
        questions=[
            QuestionPublic(
                id=question.id,
                prompt=question.prompt,
                options=[option.option_text for option in sorted(question.options, key=lambda item: item.option_key)],
            )
            for question in quiz.questions
        ],
    )


@app.post("/attempts/{attempt_id}/answers", status_code=status.HTTP_204_NO_CONTENT)
def submit_answer_endpoint(
    attempt_id: int,
    payload: AnswerSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.student)),
) -> None:
    attempt = load_attempt(db, attempt_id)
    ensure_attempt_owner_or_teacher(attempt, current_user)
    attempt = enforce_not_expired(db, attempt)
    if attempt.status == AttemptStatus.expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Attempt expired and was auto-submitted")
    submit_answer(db, attempt, payload.question_id, payload.chosen_option)


@app.post("/attempts/{attempt_id}/finish", response_model=FinishAttemptResponse)
def finish_attempt_endpoint(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.student)),
) -> FinishAttemptResponse:
    attempt = load_attempt(db, attempt_id)
    ensure_attempt_owner_or_teacher(attempt, current_user)
    attempt = enforce_not_expired(db, attempt)
    if attempt.status == AttemptStatus.expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Attempt expired and was auto-submitted")
    finished = finish_attempt(db, attempt)
    return FinishAttemptResponse(
        attempt_id=finished.id,
        status=finished.status,
        score=finished.score or 0,
        time_taken_seconds=int((finished.finished_at - finished.started_at).total_seconds()),
    )


@app.get("/attempts/{attempt_id}/result", response_model=AttemptResultResponse)
def get_attempt_result(
    attempt_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttemptResultResponse:
    attempt = load_attempt(db, attempt_id)
    ensure_attempt_owner_or_teacher(attempt, current_user)
    attempt = enforce_not_expired(db, attempt)

    answers_by_question = {answer.question_id: answer.chosen_option_key for answer in attempt.answers}
    results_by_question = {result.question_id: result for result in attempt.question_results}

    reveal_correctness = attempt.status in {AttemptStatus.finished, AttemptStatus.expired}
    breakdown = []
    for question in attempt.quiz.questions:
        chosen = answers_by_question.get(question.id)
        result = results_by_question.get(question.id)
        breakdown.append(
            AttemptBreakdownItem(
                question_id=question.id,
                chosen_option=chosen,
                is_correct=result.is_correct if reveal_correctness and result else None,
            )
        )

    time_taken = None
    if attempt.finished_at:
        time_taken = int((attempt.finished_at - attempt.started_at).total_seconds())

    return AttemptResultResponse(
        attempt_id=attempt.id,
        quiz_id=attempt.quiz_id,
        status=attempt.status,
        score=attempt.score if reveal_correctness else None,
        time_taken_seconds=time_taken,
        breakdown=breakdown,
    )


@app.get("/quizzes/{quiz_id}/attempts", response_model=list[QuizAttemptSummary])
def get_all_attempts_for_quiz(
    quiz_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.teacher)),
) -> list[QuizAttemptSummary]:
    quiz = db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz not found")
    if quiz.creator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")

    attempts = db.scalars(
        select(Attempt).where(Attempt.quiz_id == quiz_id).order_by(Attempt.id)
    ).all()
    result: list[QuizAttemptSummary] = []
    for attempt in attempts:
        user = db.get(User, attempt.user_id)
        time_taken = None
        if attempt.finished_at:
            time_taken = int((attempt.finished_at - attempt.started_at).total_seconds())
        result.append(
            QuizAttemptSummary(
                attempt_id=attempt.id,
                username=user.username,
                score=attempt.score,
                time_taken_seconds=time_taken,
                status=attempt.status,
            )
        )
    return result
