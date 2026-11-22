import os
import tempfile

# Must be set before backend.app is imported so tests never touch instance/gratten.db.
os.environ["GRATTEN_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest
from backend.app import app

GRADES = {"quiz": 90, "recitation": 90, "output": 90, "exam": 90}


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


def login(client, identity, password):
    token = client.get("/api/csrf").get_json()["csrf_token"]
    resp = client.post("/api/auth/login", json={"identity": identity, "password": password},
                       headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    return resp.get_json()["csrf_token"]


def teacher(client):
    return login(client, "teacher@olopsc.edu.ph", "Teacher123!")


def student(client):
    return login(client, "2026-8812", "Student123!")


# ---- CSRF ----------------------------------------------------------------
def test_post_without_csrf_token_is_rejected(client):
    resp = client.post("/api/auth/login", json={"identity": "x", "password": "y"})
    assert resp.status_code == 403


def test_wrong_csrf_token_is_rejected(client):
    teacher(client)
    resp = client.put("/api/grades/DB101/2026-8812", json=GRADES,
                      headers={"X-CSRF-Token": "not-the-token"})
    assert resp.status_code == 403


def test_get_requests_do_not_need_csrf(client):
    assert client.get("/api/health").status_code == 200


# ---- Teacher-to-subject assignment ---------------------------------------
def test_teacher_only_sees_assigned_subjects(client):
    teacher(client)
    ids = [s["subject_id"] for s in client.get("/api/subjects").get_json()["subjects"]]
    assert ids == ["DB101", "OOP103"]


def test_teacher_can_edit_assigned_subject(client):
    token = teacher(client)
    resp = client.put("/api/grades/DB101/2026-8812", json=GRADES, headers={"X-CSRF-Token": token})
    assert resp.status_code == 200
    assert resp.get_json()["record"]["total_grade"] == 90


def test_teacher_cannot_edit_unassigned_subject(client):
    token = teacher(client)
    resp = client.put("/api/grades/WD102/2026-8812", json=GRADES, headers={"X-CSRF-Token": token})
    assert resp.status_code == 403


def test_teacher_cannot_read_unassigned_subject(client):
    teacher(client)
    assert client.get("/api/records/WD102").status_code == 403


def test_teacher_cannot_change_attendance_in_unassigned_subject(client):
    token = teacher(client)
    resp = client.patch("/api/attendance/WD102/2026-8812", json={"field": "late", "delta": 1},
                        headers={"X-CSRF-Token": token})
    assert resp.status_code == 403
    resp = client.post("/api/attendance/WD102/2026-8812/reset", headers={"X-CSRF-Token": token})
    assert resp.status_code == 403


def test_teacher_results_only_include_assigned_subjects(client):
    teacher(client)
    data = client.get("/api/results?student_id=2026-8812").get_json()
    assert sorted(r["subject_id"] for r in data["records"]) == ["DB101", "OOP103"]


def test_student_cannot_edit_grades(client):
    token = student(client)
    resp = client.put("/api/grades/DB101/2026-8812", json=GRADES, headers={"X-CSRF-Token": token})
    assert resp.status_code == 403


def test_student_only_sees_own_record(client):
    student(client)
    records = client.get("/api/records/DB101").get_json()["records"]
    assert [r["student_id"] for r in records] == ["2026-8812"]


# ---- Attendance validation -----------------------------------------------
@pytest.mark.parametrize("body", [
    {"field": "present; DROP TABLE attendance", "delta": 1},
    {"field": ["present"], "delta": 1},
    {"field": "late", "delta": 5},
    {"field": "late", "delta": True},
    {},
])
def test_attendance_rejects_bad_input(client, body):
    token = teacher(client)
    resp = client.patch("/api/attendance/DB101/2026-8812", json=body, headers={"X-CSRF-Token": token})
    assert resp.status_code == 400


def test_attendance_increment_and_decrement(client):
    token = teacher(client)
    headers = {"X-CSRF-Token": token}
    before = client.patch("/api/attendance/DB101/2026-8813", json={"field": "late", "delta": 1},
                          headers=headers).get_json()["record"]["late"]
    after = client.patch("/api/attendance/DB101/2026-8813", json={"field": "late", "delta": -1},
                         headers=headers).get_json()["record"]["late"]
    assert after == before - 1
