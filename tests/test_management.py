"""Tests for backend/management.py using a small stand-in app with the same auth shape as app.py."""
import os
import tempfile
from functools import wraps

from flask import Flask, jsonify, session

ADMIN = "admin@olopsc.edu.ph"
TEACHER = "teacher@olopsc.edu.ph"


def world():
    os.environ["GRATTEN_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
    os.environ["GRATTEN_ADMIN_EMAIL"], os.environ["GRATTEN_ADMIN_PASSWORD"] = ADMIN, "LongPass123"
    from backend import seed
    from backend.database import get_db
    from backend.management import register
    seed.seed_demo_data()
    app = Flask(__name__)
    app.secret_key = "t"

    def current_user():
        with get_db() as db:
            row = db.execute("SELECT u.id,u.role,t.teacher_id FROM users u LEFT JOIN teachers t ON t.user_id=u.id "
                             "WHERE u.id=?", (session.get("user_id"),)).fetchone()
        return dict(row) if row else None

    def login_required(fn):
        @wraps(fn)
        def w(*a, **k):
            u = current_user()
            return (jsonify(error="auth"), 401) if not u else fn(u, *a, **k)
        return w

    def teacher_assigned(db, user, sid):
        return db.execute("SELECT 1 FROM teacher_subjects WHERE teacher_id=? AND subject_id=?",
                          (user["teacher_id"], sid)).fetchone() is not None

    @app.post("/test-login/<who>")
    def tl(who):
        with get_db() as db:
            session["user_id"] = db.execute("SELECT id FROM users WHERE login_id=?", (who,)).fetchone()["id"]
        return "ok"

    register(app, login_required, teacher_assigned)
    return app


def as_(app, who):
    c = app.test_client()
    c.post(f"/test-login/{who}")
    return c


NEW_STUDENT = {"role": "Student", "login_id": "2027-0001", "name": "Juan Dela Cruz", "course": "BSCS",
               "email": "juan@olopsc.edu.ph", "password": "Passw0rd!x"}


def test_sections_admin_creates_staff_lists_student_blocked():
    app = world()
    admin, teacher, student = as_(app, ADMIN), as_(app, TEACHER), as_(app, "2026-8812")
    assert admin.post("/api/admin/sections", json={"code": "BSCS 1-A", "program": "BSCS", "year_level": 1}).status_code == 201
    assert admin.post("/api/admin/sections", json={"code": "BSCS 1-A", "program": "BSCS"}).status_code == 409
    assert admin.post("/api/admin/sections", json={"code": "X", "program": "Y", "year_level": 9}).status_code == 400
    assert "BSCS 1-A" in [s["code"] for s in teacher.get("/api/sections").get_json()["sections"]]
    assert student.get("/api/sections").status_code == 403
    assert teacher.post("/api/admin/sections", json={"code": "Z", "program": "Z"}).status_code == 403


def test_terms_year_validation_and_current_switch():
    app = world()
    admin = as_(app, ADMIN)
    assert admin.post("/api/admin/terms", json={"academic_year": "2027-2029"}).status_code == 400
    assert admin.post("/api/admin/terms", json={"academic_year": "2027-2028"}).status_code == 201
    assert admin.post("/api/admin/terms", json={"academic_year": "2027-2028"}).status_code == 409
    years = admin.get("/api/terms").get_json()["academic_years"]
    second = next(t for y in years if y["label"] == "2027-2028" for t in y["terms"] if t["semester"] == "Second Semester")
    assert admin.put("/api/admin/terms/current", json={"term_id": second["term_id"]}).status_code == 200
    assert admin.get("/api/terms").get_json()["current_term_id"] == second["term_id"]
    assert admin.put("/api/admin/terms/current", json={"term_id": 99999}).status_code == 404


def test_create_user_validation_duplicates_and_hashing():
    app = world()
    admin = as_(app, ADMIN)
    assert admin.post("/api/admin/users", json={**NEW_STUDENT, "password": "short"}).status_code == 400
    assert admin.post("/api/admin/users", json={**NEW_STUDENT, "role": "Admin"}).status_code == 400
    assert admin.post("/api/admin/users", json=NEW_STUDENT).status_code == 201
    assert admin.post("/api/admin/users", json=NEW_STUDENT).status_code == 409
    from backend.database import get_db
    with get_db() as db:
        h = db.execute("SELECT password_hash FROM users WHERE login_id='2027-0001'").fetchone()[0]
    assert "Passw0rd!x" not in h
    assert as_(app, TEACHER).post("/api/admin/users", json=NEW_STUDENT).status_code == 403


def test_enrollment_rules():
    app = world()
    admin, teacher = as_(app, ADMIN), as_(app, TEACHER)
    sec = admin.post("/api/admin/sections", json={"code": "BSCS 1-A", "program": "BSCS", "year_level": 1}).get_json()["section"]
    admin.post("/api/admin/users", json=NEW_STUDENT)
    # no section yet -> rejected
    assert teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "DB101"}).status_code == 400
    ok = teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "DB101", "section_id": sec["section_id"]})
    assert ok.status_code == 201
    assert teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "DB101", "section_id": sec["section_id"]}).status_code == 409
    assert teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "WD102", "section_id": sec["section_id"]}).status_code == 403
    assert teacher.post("/api/enrollments", json={"student_id": "nope", "subject_id": "DB101", "section_id": sec["section_id"]}).status_code == 404
    lst = teacher.get(f"/api/enrollments?subject_id=DB101&section_id={sec['section_id']}&q=juan").get_json()["enrollments"]
    assert [e["student_id"] for e in lst] == ["2027-0001"]
    assert teacher.get("/api/enrollments").status_code == 403           # teachers must name an assigned subject
    assert as_(app, "2026-8812").post("/api/enrollments", json={"student_id": "2026-8812", "subject_id": "DB101"}).status_code == 403
    from backend.database import get_db
    with get_db() as db:   # grade + attendance rows exist and attendance starts blank
        eid = ok.get_json()["enrollment_id"]
        assert db.execute("SELECT present+late+absent FROM attendance WHERE enrollment_id=?", (eid,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM grades WHERE enrollment_id=?", (eid,)).fetchone()[0] == 1
    assert teacher.delete(f"/api/enrollments/{eid}").status_code == 200
    assert teacher.delete(f"/api/enrollments/{eid}").status_code == 404


def test_semesters_are_kept_separate():
    app = world()
    admin, teacher = as_(app, ADMIN), as_(app, TEACHER)
    sec = admin.post("/api/admin/sections", json={"code": "BSCS 1-A", "program": "BSCS"}).get_json()["section"]
    admin.post("/api/admin/users", json=NEW_STUDENT)
    assert teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "DB101", "section_id": sec["section_id"]}).status_code == 201
    years = admin.get("/api/terms").get_json()["academic_years"]
    second = next(t for y in years if y["label"] == "2026-2027" for t in y["terms"] if t["semester"] == "Second Semester")
    admin.put("/api/admin/terms/current", json={"term_id": second["term_id"]})
    assert teacher.post("/api/enrollments", json={"student_id": "2027-0001", "subject_id": "DB101", "section_id": sec["section_id"]}).status_code == 201
    now = teacher.get("/api/enrollments?subject_id=DB101&q=2027-0001").get_json()
    assert len(now["enrollments"]) == 1 and now["enrollments"][0]["semester"] == "Second Semester"


def test_teacher_subject_assignment_and_subject_creation():
    app = world()
    admin = as_(app, ADMIN)
    assert admin.post("/api/admin/subjects", json={"subject_id": "ENG101", "code": "ENG", "name": "English", "instructor": "T"}).status_code == 201
    assert admin.post("/api/admin/subjects", json={"subject_id": "ENG101", "code": "ENG", "name": "English", "instructor": "T"}).status_code == 409
    assert admin.post("/api/admin/teacher-subjects", json={"teacher_id": 1, "subject_id": "ENG101"}).status_code == 200
    assert as_(app, TEACHER).get("/api/enrollments?subject_id=ENG101").status_code == 200
    assert admin.delete("/api/admin/teacher-subjects", json={"teacher_id": 1, "subject_id": "ENG101"}).status_code == 200
    assert as_(app, TEACHER).get("/api/enrollments?subject_id=ENG101").status_code == 403
