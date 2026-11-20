"""Tests for saving questions (e.g. AI-generated ones) to the question bank."""

import json
import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from conftest import _real_sqlite3_connect


def _ai_question(text="What is the largest planet?", category="astronomy", correct="b"):
    return {
        "id": 1000,
        "text": text,
        "options": [
            {"id": "a", "text": "Mars"},
            {"id": "b", "text": "Jupiter"},
            {"id": "c", "text": "Venus"},
            {"id": "d", "text": "Earth"},
        ],
        "correct_answer": correct,
        "category": category,
    }


@pytest.fixture
def client(sample_db):
    from main import app

    with patch("sqlite3.connect", side_effect=lambda _: _real_sqlite3_connect(sample_db)):
        yield TestClient(app)


def _question_rows(db_path):
    conn = _real_sqlite3_connect(db_path)
    rows = conn.execute("SELECT text, options, correct_answer, category FROM questions ORDER BY id").fetchall()
    conn.close()
    return rows


def test_saves_new_questions(client, sample_db):
    response = client.post("/api/questions/bank", json={"questions": [_ai_question()]})

    assert response.status_code == 200
    body = response.json()
    assert body["saved"] == 1
    assert body["skipped"] == 0
    saved = body["questions"][0]
    assert saved["id"] != 1000
    assert saved["category"] == "astronomy"

    rows = _question_rows(sample_db)
    assert len(rows) == 2
    text, options, correct, category = rows[-1]
    assert text == "What is the largest planet?"
    assert json.loads(options)[1] == {"id": "b", "text": "Jupiter"}
    assert correct == "b"
    assert category == "astronomy"


def test_saved_questions_are_served_from_database(client):
    client.post("/api/questions/bank", json={"questions": [_ai_question()]})

    response = client.get("/api/questions?category=astronomy&limit=5")

    assert response.status_code == 200
    assert [q["text"] for q in response.json()] == ["What is the largest planet?"]
    assert "astronomy" in client.get("/api/categories").json()["categories"]


def test_skips_duplicates_case_insensitively(client, sample_db):
    existing = _ai_question(text="  what is the capital of FRANCE?  ", category="Geography", correct="c")
    payload = {"questions": [existing, _ai_question(), _ai_question()]}

    response = client.post("/api/questions/bank", json=payload)

    body = response.json()
    assert body["saved"] == 1
    assert body["skipped"] == 2
    assert len(_question_rows(sample_db)) == 2


def test_skips_unicode_case_variants(client, sample_db):
    payload = {"questions": [_ai_question(text="École history?"), _ai_question(text="école HISTORY?")]}

    response = client.post("/api/questions/bank", json=payload)

    assert response.json()["saved"] == 1
    assert response.json()["skipped"] == 1
    assert len(_question_rows(sample_db)) == 2


def test_concurrent_saves_do_not_create_duplicates(client, sample_db):
    from concurrent.futures import ThreadPoolExecutor

    payload = {"questions": [_ai_question()]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.post("/api/questions/bank", json=payload).json(), range(8)))

    assert sum(r["saved"] for r in results) == 1
    assert len(_question_rows(sample_db)) == 2


def test_category_is_normalized(client):
    response = client.post("/api/questions/bank", json={"questions": [_ai_question(category="  World History ")]})

    assert response.json()["questions"][0]["category"] == "world history"


@pytest.mark.parametrize(
    "mutate, detail",
    [
        (lambda q: q.update(text="   "), "Question text must not be empty"),
        (lambda q: q.update(correct_answer="e"), "Correct answer must be one of the option IDs"),
        (lambda q: q["options"].pop(), "Options must have unique IDs 'a', 'b', 'c', 'd'"),
        (lambda q: q["options"][0].update(id="b"), "Options must have unique IDs 'a', 'b', 'c', 'd'"),
    ],
)
def test_rejects_invalid_questions_without_saving_any(client, sample_db, mutate, detail):
    bad = _ai_question(text="Bad question?")
    mutate(bad)

    response = client.post("/api/questions/bank", json={"questions": [_ai_question(), bad]})

    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert len(_question_rows(sample_db)) == 1


def test_rejects_empty_request(client):
    response = client.post("/api/questions/bank", json={"questions": []})

    assert response.status_code == 400


def test_rejects_too_many_questions(client):
    questions = [_ai_question(text=f"Question {i}?") for i in range(101)]

    response = client.post("/api/questions/bank", json={"questions": questions})

    assert response.status_code == 400
