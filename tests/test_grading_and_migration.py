import os
import sqlite3
import tempfile

import pytest

from backend.logic import summarize_grades, validate_score


def test_not_all_assessments_are_out_of_100():
    s = summarize_grades({"output": [(40, 50), (30, 50)], "quiz": [(9, 10)], "exam": [(45, 60)]}, 80)
    assert s["components"]["output"]["percentage"] == 70.0
    assert s["components"]["quiz"]["percentage"] == 90.0
    assert s["components"]["exam"]["percentage"] == 75.0
    assert s["final_grade"] == round(70 * .25 + 90 * .20 + 80 * .15 + 75 * .40, 2)


def test_empty_category_blocks_final_grade():
    s = summarize_grades({"output": [(10, 10)], "quiz": [(10, 10)], "exam": []}, 90)
    assert s["final_grade"] is None and s["completion"] == "Incomplete"


def test_ungraded_assessment_is_not_a_perfect_score():
    s = summarize_grades({"output": [(5, 10), (None, 10)], "quiz": [(1, 1)], "exam": [(1, 1)]}, 100)
    assert s["components"]["output"]["status"] == "partial"
    assert s["components"]["output"]["percentage"] == 50.0
    assert s["final_grade"] is None


def test_nothing_graded():
    s = summarize_grades({})
    assert s["completion"] == "Not yet graded" and s["final_grade"] is None


@pytest.mark.parametrize("earned,mx", [(-1, 10), (11, 10), (5, 0), (True, 10), (float("nan"), 10), ("5", 10)])
def test_validate_score_rejects_bad_input(earned, mx):
    with pytest.raises(ValueError):
        validate_score(earned, mx)


def test_migration_preserves_legacy_data(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "legacy.db")
    monkeypatch.setenv("GRATTEN_DB", path)
    from backend import database
    # Build a v1-style database with the OLD users CHECK, then migrate it.
    conn = sqlite3.connect(path)
    conn.executescript(database.BASE_SCHEMA.replace(",'Admin'", ""))
    conn.execute("INSERT INTO users(login_id,email,password_hash,role) VALUES('s1','s1@x.ph','h','Student')")
    conn.execute("INSERT INTO students(student_id,user_id,name,course) VALUES('s1',1,'A','BSIT')")
    conn.execute("INSERT INTO subjects VALUES('DB101','FDMS','DB',3,'T')")
    conn.execute("INSERT INTO enrollments(student_id,subject_id) VALUES('s1','DB101')")
    conn.execute("INSERT INTO grades(enrollment_id,quiz,recitation,output,exam) VALUES(1,80,90,70,60)")
    conn.execute("INSERT INTO attendance(enrollment_id,present,late,absent) VALUES(1,10,2,1)")
    conn.commit(); conn.close()

    database.init_db()
    database.init_db()  # idempotent
    with database.get_db() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        db.execute("INSERT INTO users(login_id,password_hash,role) VALUES('a','h','Admin')")
        assert db.execute("SELECT absent FROM attendance").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM assessment_scores").fetchone()[0] == 3
        assert db.execute("SELECT score FROM recitation_scores").fetchone()[0] == 90
        assert db.execute("SELECT term_id,section_id FROM enrollments").fetchone()[0] is not None
        assert db.execute("SELECT COUNT(*) FROM attendance_sessions").fetchone()[0] == 0
