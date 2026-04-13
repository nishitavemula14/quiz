from datetime import timedelta

from app import services


def register_user(client, username: str, password: str, role: str) -> None:
    response = client.post("/users", json={"username": username, "password": password, "role": role})
    assert response.status_code == 201, response.text


def auth_headers(client, username: str, role: str) -> dict[str, str]:
    password = f"{username}-pass"
    register_user(client, username, password, role)
    response = client.post("/auth/token", json={"username": username, "password": password})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_sample_quiz(client, teacher_headers: dict[str, str], time_limit_minutes: int = 10) -> int:
    payload = {
        "title": "Basics",
        "description": "Simple quiz",
        "time_limit_minutes": time_limit_minutes,
        "questions": [
            {
                "prompt": "2 + 2 = ?",
                "options": ["3", "4", "5", "6"],
                "correct_option": "B",
            },
            {
                "prompt": "Capital of France?",
                "options": ["Berlin", "Madrid", "Paris", "Rome"],
                "correct_option": "C",
            },
        ],
    }
    response = client.post("/quizzes", json=payload, headers=teacher_headers)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_full_quiz_flow(client):
    teacher_headers = auth_headers(client, "teacher1", "teacher")
    student_headers = auth_headers(client, "student1", "student")

    quiz_id = create_sample_quiz(client, teacher_headers)

    start_response = client.post(f"/quizzes/{quiz_id}/attempts", headers=student_headers)
    assert start_response.status_code == 200
    start_body = start_response.json()
    attempt_id = start_body["attempt_id"]
    first_question_id = start_body["questions"][0]["id"]
    second_question_id = start_body["questions"][1]["id"]

    active_result = client.get(f"/attempts/{attempt_id}/result", headers=student_headers)
    assert active_result.status_code == 200
    assert active_result.json()["score"] is None
    assert active_result.json()["breakdown"][0]["is_correct"] is None

    first_answer = client.post(
        f"/attempts/{attempt_id}/answers",
        json={"question_id": first_question_id, "chosen_option": "A"},
        headers=student_headers,
    )
    assert first_answer.status_code == 204

    overwrite_answer = client.post(
        f"/attempts/{attempt_id}/answers",
        json={"question_id": first_question_id, "chosen_option": "B"},
        headers=student_headers,
    )
    assert overwrite_answer.status_code == 204

    second_answer = client.post(
        f"/attempts/{attempt_id}/answers",
        json={"question_id": second_question_id, "chosen_option": "C"},
        headers=student_headers,
    )
    assert second_answer.status_code == 204

    finish_response = client.post(f"/attempts/{attempt_id}/finish", headers=student_headers)
    assert finish_response.status_code == 200
    assert finish_response.json()["score"] == 100

    result_response = client.get(f"/attempts/{attempt_id}/result", headers=student_headers)
    assert result_response.status_code == 200
    assert result_response.json()["score"] == 100
    assert result_response.json()["breakdown"] == [
        {"question_id": first_question_id, "chosen_option": "B", "is_correct": True},
        {"question_id": second_question_id, "chosen_option": "C", "is_correct": True},
    ]

    teacher_attempts = client.get(f"/quizzes/{quiz_id}/attempts", headers=teacher_headers)
    assert teacher_attempts.status_code == 200
    assert teacher_attempts.json()[0]["username"] == "student1"
    assert teacher_attempts.json()[0]["score"] == 100

    locked_answer = client.post(
        f"/attempts/{attempt_id}/answers",
        json={"question_id": first_question_id, "chosen_option": "A"},
        headers=student_headers,
    )
    assert locked_answer.status_code == 409


def test_lazy_expiry_auto_submits_and_rejects_new_answer(client, monkeypatch):
    teacher_headers = auth_headers(client, "teacher2", "teacher")
    student_headers = auth_headers(client, "student2", "student")
    quiz_id = create_sample_quiz(client, teacher_headers, time_limit_minutes=1)

    start_response = client.post(f"/quizzes/{quiz_id}/attempts", headers=student_headers)
    start_body = start_response.json()
    attempt_id = start_body["attempt_id"]
    first_question_id = start_body["questions"][0]["id"]
    second_question_id = start_body["questions"][1]["id"]

    original_now = services.utc_now()
    monkeypatch.setattr(services, "utc_now", lambda: original_now + timedelta(minutes=2))

    answer_response = client.post(
        f"/attempts/{attempt_id}/answers",
        json={"question_id": first_question_id, "chosen_option": "B"},
        headers=student_headers,
    )
    assert answer_response.status_code == 409
    assert answer_response.json()["detail"] == "Attempt expired and was auto-submitted"

    result_response = client.get(f"/attempts/{attempt_id}/result", headers=student_headers)
    assert result_response.status_code == 200
    body = result_response.json()
    assert body["status"] == "expired"
    assert body["score"] == 0
    assert body["breakdown"] == [
        {"question_id": first_question_id, "chosen_option": None, "is_correct": False},
        {"question_id": second_question_id, "chosen_option": None, "is_correct": False},
    ]


def test_teacher_cannot_take_own_quiz_and_student_cannot_create_quiz(client):
    teacher_headers = auth_headers(client, "teacher3", "teacher")
    student_headers = auth_headers(client, "student3", "student")
    quiz_id = create_sample_quiz(client, teacher_headers)

    teacher_attempt = client.post(f"/quizzes/{quiz_id}/attempts", headers=teacher_headers)
    assert teacher_attempt.status_code == 403

    create_as_student = client.post(
        "/quizzes",
        json={
            "title": "Blocked",
            "description": "No access",
            "time_limit_minutes": 5,
            "questions": [
                {
                    "prompt": "Q",
                    "options": ["1", "2", "3", "4"],
                    "correct_option": "A",
                }
            ],
        },
        headers=student_headers,
    )
    assert create_as_student.status_code == 403


def test_registration_requires_login_password_and_prevents_duplicates(client):
    register_response = client.post(
        "/users",
        json={"username": "teacher4", "password": "teacher4-pass", "role": "teacher"},
    )
    assert register_response.status_code == 201

    duplicate_response = client.post(
        "/users",
        json={"username": "teacher4", "password": "teacher4-pass", "role": "teacher"},
    )
    assert duplicate_response.status_code == 409

    bad_login = client.post(
        "/auth/token",
        json={"username": "teacher4", "password": "wrong-pass"},
    )
    assert bad_login.status_code == 401

    good_login = client.post(
        "/auth/token",
        json={"username": "teacher4", "password": "teacher4-pass"},
    )
    assert good_login.status_code == 200
    assert "access_token" in good_login.json()
