import hmac
import os
import secrets
from functools import wraps
from .config import load_env_file, env_flag

load_env_file()

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash
from .database import get_db
from .logic import (
    calculate_attendance,
    calculate_grade_breakdown,
    calculate_total_grade,
    is_valid_number,
    validate_points,
    validate_recitation,
)
from .seed import seed_demo_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SECRET = "dev-only-change-this-secret"

app = Flask(__name__, static_folder="../frontend/static", static_url_path="/static")
app.secret_key = os.environ.get("GRATTEN_SECRET_KEY", DEFAULT_SECRET)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("COOKIE_SECURE"),
)
if app.config["SESSION_COOKIE_SECURE"] and app.secret_key == DEFAULT_SECRET:
    raise RuntimeError("Set GRATTEN_SECRET_KEY to a long random value when COOKIE_SECURE=1.")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def new_csrf_token():
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def ensure_csrf_token():
    return session.get("csrf_token") or new_csrf_token()


@app.before_request
def csrf_protect():
    if request.method in SAFE_METHODS:
        return None
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        return jsonify(error="Invalid or missing CSRF token."), 403
    return None


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT u.id,u.login_id,u.email,u.role,s.student_id,s.name,s.course,"
            "t.teacher_id,t.name AS teacher_name "
            "FROM users u LEFT JOIN students s ON s.user_id=u.id "
            "LEFT JOIN teachers t ON t.user_id=u.id WHERE u.id=? AND u.active=1",
            (uid,),
        ).fetchone()
        if not row:
            session.clear()
            return None
        return dict(row)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify(error="Authentication required."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def teacher_required(fn):
    @wraps(fn)
    def wrapper(user, *args, **kwargs):
        if user["role"] != "Teacher":
            return jsonify(error="Teacher access required."), 403
        return fn(user, *args, **kwargs)
    return wrapper


def teacher_subject_ids(db, user):
    rows = db.execute(
        "SELECT subject_id FROM teacher_subjects WHERE teacher_id=?",
        (user["teacher_id"],),
    ).fetchall()
    return [r["subject_id"] for r in rows]


def teacher_assigned(db, user, subject_id):
    return (
        db.execute(
            "SELECT 1 FROM teacher_subjects WHERE teacher_id=? AND subject_id=?",
            (user["teacher_id"], subject_id),
        ).fetchone()
        is not None
    )


def not_assigned_response():
    return jsonify(error="You are not assigned to this subject."), 403


def _sync_grades_cache(db, enrollment_id, breakdown):
    comps = breakdown["components"]
    out_pct = comps["output"]["percentage"] if comps["output"]["is_complete"] else 0.0
    quiz_pct = comps["quiz"]["percentage"] if comps["quiz"]["is_complete"] else 0.0
    rec_pct = comps["recitation"]["percentage"] if comps["recitation"]["is_complete"] else 0.0
    exam_pct = comps["exam"]["percentage"] if comps["exam"]["is_complete"] else 0.0
    db.execute(
        """
        UPDATE grades
        SET output=?, quiz=?, recitation=?, exam=?, updated_at=CURRENT_TIMESTAMP
        WHERE enrollment_id=?
        """,
        (out_pct, quiz_pct, rec_pct, exam_pct, enrollment_id),
    )


def _recalculate_subject_grades(db, subject_id):
    enrollments = db.execute(
        """
        SELECT e.enrollment_id, g.recitation
        FROM enrollments e
        LEFT JOIN grades g ON g.enrollment_id = e.enrollment_id
        WHERE e.subject_id=?
        """,
        (subject_id,),
    ).fetchall()
    assessments = db.execute(
        "SELECT id, name, category, max_points, created_at FROM assessments WHERE subject_id=? ORDER BY id",
        (subject_id,),
    ).fetchall()
    scores = db.execute(
        """
        SELECT sc.assessment_id, sc.enrollment_id, sc.earned_points
        FROM assessment_scores sc
        JOIN assessments a ON a.id = sc.assessment_id
        WHERE a.subject_id=?
        """,
        (subject_id,),
    ).fetchall()

    score_map = {(s["assessment_id"], s["enrollment_id"]): s["earned_points"] for s in scores}

    for enr in enrollments:
        eid = enr["enrollment_id"]
        grouped = {"Output": [], "Quiz": [], "Exam": []}
        for a in assessments:
            grouped[a["category"]].append({
                "id": a["id"],
                "name": a["name"],
                "category": a["category"],
                "max_points": a["max_points"],
                "earned_points": score_map.get((a["id"], eid)),
            })
        breakdown = calculate_grade_breakdown(grouped, enr["recitation"])
        _sync_grades_cache(db, eid, breakdown)


def record_for(db, student_id, subject_id):
    row = db.execute(
        """
        SELECT e.enrollment_id, e.student_id, e.subject_id,
               g.quiz, g.recitation, g.output, g.exam,
               a.present, a.late, a.absent,
               s.name AS student_name, s.course,
               sub.code, sub.name AS subject_name, sub.instructor
        FROM enrollments e
        JOIN grades g ON g.enrollment_id = e.enrollment_id
        JOIN attendance a ON a.enrollment_id = e.enrollment_id
        JOIN students s ON s.student_id = e.student_id
        JOIN subjects sub ON sub.subject_id = e.subject_id
        WHERE e.student_id=? AND e.subject_id=?
        """,
        (student_id, subject_id),
    ).fetchone()
    if not row:
        return None

    eid = row["enrollment_id"]
    assessments = db.execute(
        "SELECT id, name, category, max_points, created_at FROM assessments WHERE subject_id=? ORDER BY id",
        (subject_id,),
    ).fetchall()
    scores = db.execute(
        "SELECT assessment_id, earned_points FROM assessment_scores WHERE enrollment_id=?",
        (eid,),
    ).fetchall()
    score_by_aid = {sc["assessment_id"]: sc["earned_points"] for sc in scores}

    grouped = {"Output": [], "Quiz": [], "Exam": []}
    for a in assessments:
        grouped[a["category"]].append({
            "id": a["id"],
            "name": a["name"],
            "category": a["category"],
            "max_points": a["max_points"],
            "earned_points": score_by_aid.get(a["id"]),
        })

    breakdown = calculate_grade_breakdown(grouped, row["recitation"])
    comps = breakdown["components"]

    data = dict(row)
    data["breakdown"] = breakdown
    data["is_grade_complete"] = breakdown["is_complete"]
    data["output"] = comps["output"]["percentage"]
    data["quiz"] = comps["quiz"]["percentage"]
    data["recitation"] = comps["recitation"]["percentage"]
    data["exam"] = comps["exam"]["percentage"]
    data["total_grade"] = calculate_total_grade(data)
    data["attendance"] = calculate_attendance(data)
    return data


@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "frontend"), "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", database="sqlite")


@app.get("/api/csrf")
def csrf():
    return jsonify(csrf_token=ensure_csrf_token())


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    identity = str(payload.get("identity", "")).strip()
    password = str(payload.get("password", ""))
    with get_db() as db:
        user = db.execute(
            "SELECT id, login_id, email, password_hash, role FROM users "
            "WHERE (login_id=? OR email=?) AND active=1",
            (identity, identity),
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify(error="Invalid ID/email or password."), 401
        session.clear()
        session["user_id"] = user["id"]
        token = new_csrf_token()
        return jsonify(user=current_user(), csrf_token=token)


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(message="Logged out.", csrf_token=new_csrf_token())


@app.get("/api/auth/me")
@login_required
def me(user):
    return jsonify(user=user, csrf_token=ensure_csrf_token())


@app.get("/api/subjects")
@login_required
def subjects(user):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute(
                """
                SELECT DISTINCT s.subject_id, s.code, s.name, s.units, s.instructor
                FROM subjects s JOIN enrollments e ON e.subject_id=s.subject_id
                WHERE e.student_id=? ORDER BY s.subject_id
                """,
                (user["student_id"],),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT s.* FROM subjects s
                JOIN teacher_subjects ts ON ts.subject_id=s.subject_id
                WHERE ts.teacher_id=? ORDER BY s.subject_id
                """,
                (user["teacher_id"],),
            ).fetchall()
        return jsonify(subjects=[dict(r) for r in rows])


@app.get("/api/students")
@login_required
def students(user):
    if user["role"] != "Teacher":
        return jsonify(
            students=[{
                "student_id": user["student_id"],
                "name": user["name"],
                "course": user["course"],
            }]
        )
    with get_db() as db:
        rows = db.execute(
            """
            SELECT DISTINCT st.student_id, st.name, st.course
            FROM students st
            JOIN enrollments e ON e.student_id=st.student_id
            JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
            WHERE ts.teacher_id=? ORDER BY st.name
            """,
            (user["teacher_id"],),
        ).fetchall()
        return jsonify(students=[dict(r) for r in rows])


@app.get("/api/records/<subject_id>")
@login_required
def records(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? AND student_id=?",
                (subject_id, user["student_id"]),
            ).fetchall()
        else:
            if not teacher_assigned(db, user, subject_id):
                return not_assigned_response()
            rows = db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()

        output = [record_for(db, r["student_id"], subject_id) for r in rows]
        assessments = [
            dict(a)
            for a in db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE subject_id=? ORDER BY id",
                (subject_id,),
            ).fetchall()
        ]
        section_rows = db.execute(
            """
            SELECT st.course AS section_name, COUNT(*) AS student_count
            FROM enrollments e
            JOIN students st ON st.student_id = e.student_id
            WHERE e.subject_id=?
            GROUP BY st.course
            ORDER BY st.course
            """,
            (subject_id,),
        ).fetchall()
        sections = [dict(s) for s in section_rows]
        return jsonify(records=output, assessments=assessments, sections=sections)


@app.get("/api/assessments/<subject_id>")
@login_required
def list_assessments(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            enrolled = db.execute(
                "SELECT 1 FROM enrollments WHERE subject_id=? AND student_id=?",
                (subject_id, user["student_id"]),
            ).fetchone()
            if not enrolled:
                return jsonify(error="Not enrolled in this subject."), 403
        else:
            if not teacher_assigned(db, user, subject_id):
                return not_assigned_response()
        rows = db.execute(
            "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
            "FROM assessments WHERE subject_id=? ORDER BY id",
            (subject_id,),
        ).fetchall()
        return jsonify(assessments=[dict(r) for r in rows])


@app.post("/api/assessments/<subject_id>")
@login_required
@teacher_required
def create_assessment(user, subject_id):
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    category = str(payload.get("category", "")).strip()
    max_points = payload.get("max_points")

    if not name:
        return jsonify(error="Assessment name is required."), 400
    if category not in ("Output", "Quiz", "Exam"):
        return jsonify(error="Category must be Output, Quiz, or Exam."), 400
    if not is_valid_number(max_points) or float(max_points) <= 0:
        return jsonify(error="Maximum points must be greater than zero."), 400

    max_pts_val = float(max_points)
    scores_payload = payload.get("scores") or {}

    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        enrollments = db.execute(
            "SELECT enrollment_id, student_id FROM enrollments WHERE subject_id=?",
            (subject_id,),
        ).fetchall()
        enr_by_student = {e["student_id"]: e["enrollment_id"] for e in enrollments}

        validated_scores = []
        if isinstance(scores_payload, dict):
            for sid, pts in scores_payload.items():
                if pts is None or str(pts).strip() == "":
                    continue
                if sid not in enr_by_student:
                    return jsonify(error=f"Student {sid} is not enrolled in {subject_id}."), 400
                ok, err = validate_points(pts, max_pts_val)
                if not ok:
                    return jsonify(error=err), 400
                validated_scores.append((enr_by_student[sid], float(pts)))

        cur = db.execute(
            "INSERT INTO assessments (subject_id, teacher_id, name, category, max_points) VALUES (?,?,?,?,?)",
            (subject_id, user["teacher_id"], name, category, max_pts_val),
        )
        assessment_id = cur.lastrowid

        for eid, pts_val in validated_scores:
            db.execute(
                "INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points) VALUES (?,?,?)",
                (assessment_id, eid, pts_val),
            )

        _recalculate_subject_grades(db, subject_id)
        assessment = dict(
            db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE id=?",
                (assessment_id,),
            ).fetchone()
        )
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(assessment=assessment, records=updated_records), 201


@app.put("/api/assessments/<subject_id>/<int:assessment_id>")
@login_required
@teacher_required
def update_assessment(user, subject_id, assessment_id):
    payload = request.get_json(silent=True) or {}
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        existing = db.execute(
            "SELECT id, subject_id, teacher_id, name, category, max_points "
            "FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        ).fetchone()
        if not existing:
            return jsonify(error="Assessment not found."), 404

        name = str(payload.get("name", existing["name"])).strip()
        category = str(payload.get("category", existing["category"])).strip()
        max_points = payload.get("max_points", existing["max_points"])

        if not name:
            return jsonify(error="Assessment name is required."), 400
        if category not in ("Output", "Quiz", "Exam"):
            return jsonify(error="Category must be Output, Quiz, or Exam."), 400
        if not is_valid_number(max_points) or float(max_points) <= 0:
            return jsonify(error="Maximum points must be greater than zero."), 400

        max_pts_val = float(max_points)

        existing_scores = db.execute(
            "SELECT id, enrollment_id, earned_points FROM assessment_scores WHERE assessment_id=?",
            (assessment_id,),
        ).fetchall()
        for sc in existing_scores:
            if float(sc["earned_points"]) > max_pts_val:
                return jsonify(
                    error=f"An existing score ({sc['earned_points']}) exceeds the new maximum points ({max_pts_val})."
                ), 400

        scores_payload = payload.get("scores")
        validated_updates = []
        if isinstance(scores_payload, dict):
            enrollments = db.execute(
                "SELECT enrollment_id, student_id FROM enrollments WHERE subject_id=?",
                (subject_id,),
            ).fetchall()
            enr_by_student = {e["student_id"]: e["enrollment_id"] for e in enrollments}
            for sid, pts in scores_payload.items():
                if sid not in enr_by_student:
                    return jsonify(error=f"Student {sid} is not enrolled in {subject_id}."), 400
                if pts is None or str(pts).strip() == "":
                    validated_updates.append((enr_by_student[sid], None))
                else:
                    ok, err = validate_points(pts, max_pts_val)
                    if not ok:
                        return jsonify(error=err), 400
                    validated_updates.append((enr_by_student[sid], float(pts)))

        db.execute(
            "UPDATE assessments SET name=?, category=?, max_points=? WHERE id=? AND subject_id=?",
            (name, category, max_pts_val, assessment_id, subject_id),
        )

        for eid, pts_val in validated_updates:
            if pts_val is None:
                db.execute(
                    "DELETE FROM assessment_scores WHERE assessment_id=? AND enrollment_id=?",
                    (assessment_id, eid),
                )
            else:
                db.execute(
                    """
                    INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                    VALUES (?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(assessment_id, enrollment_id)
                    DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                    """,
                    (assessment_id, eid, pts_val),
                )

        _recalculate_subject_grades(db, subject_id)
        updated_assessment = dict(
            db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE id=?",
                (assessment_id,),
            ).fetchone()
        )
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(assessment=updated_assessment, records=updated_records)


@app.delete("/api/assessments/<subject_id>/<int:assessment_id>")
@login_required
@teacher_required
def delete_assessment(user, subject_id, assessment_id):
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        existing = db.execute(
            "SELECT id FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        ).fetchone()
        if not existing:
            return jsonify(error="Assessment not found."), 404

        db.execute(
            "DELETE FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        )
        _recalculate_subject_grades(db, subject_id)
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(deleted=True, assessment_id=assessment_id, records=updated_records)


@app.put("/api/grades/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_grade(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}

    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        enr = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not enr:
            return jsonify(error="Enrollment not found."), 404
        eid = enr["enrollment_id"]

        legacy_keys = ("quiz", "recitation", "output", "exam")
        if all(k in payload for k in legacy_keys) and "assessment_scores" not in payload:
            try:
                values = [float(payload[k]) for k in legacy_keys]
            except (TypeError, ValueError):
                return jsonify(error="Grades must be numeric."), 400
            if any(not is_valid_number(v) or v < 0 or v > 100 for v in values):
                return jsonify(error="Each grade must be between 0 and 100."), 400

            db.execute(
                """
                UPDATE grades
                SET quiz=?, recitation=?, output=?, exam=?, updated_at=CURRENT_TIMESTAMP
                WHERE enrollment_id=?
                """,
                (*values, eid),
            )
            subj_assessments = db.execute(
                "SELECT id, category, max_points FROM assessments WHERE subject_id=?",
                (subject_id,),
            ).fetchall()
            cat_counts = {}
            for a in subj_assessments:
                cat_counts[a["category"]] = cat_counts.get(a["category"], 0) + 1
            col_map = {"Output": values[2], "Quiz": values[0], "Exam": values[3]}
            for a in subj_assessments:
                if cat_counts.get(a["category"]) == 1:
                    scaled = round((col_map[a["category"]] / 100.0) * float(a["max_points"]), 2)
                    db.execute(
                        """
                        INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                        VALUES (?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(assessment_id, enrollment_id)
                        DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                        """,
                        (a["id"], eid, scaled),
                    )
            updated = record_for(db, student_id, subject_id)
            return jsonify(record=updated)

        if "recitation" in payload and payload["recitation"] is not None and str(payload["recitation"]).strip() != "":
            ok, err = validate_recitation(payload["recitation"])
            if not ok:
                return jsonify(error=err), 400
            db.execute(
                "UPDATE grades SET recitation=?, updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
                (float(payload["recitation"]), eid),
            )

        if "assessment_scores" in payload:
            items = payload["assessment_scores"]
            if not isinstance(items, list):
                return jsonify(error="assessment_scores must be a list."), 400

            parsed = []
            for entry in items:
                if not isinstance(entry, dict) or "assessment_id" not in entry:
                    return jsonify(error="Each score entry requires assessment_id."), 400
                aid = entry["assessment_id"]
                assess_row = db.execute(
                    "SELECT id, max_points FROM assessments WHERE id=? AND subject_id=?",
                    (aid, subject_id),
                ).fetchone()
                if not assess_row:
                    return jsonify(error=f"Assessment {aid} does not belong to {subject_id}."), 400

                raw_pts = entry.get("earned_points")
                if raw_pts is None or str(raw_pts).strip() == "":
                    parsed.append((assess_row["id"], None))
                else:
                    ok, err = validate_points(raw_pts, assess_row["max_points"])
                    if not ok:
                        return jsonify(error=err), 400
                    parsed.append((assess_row["id"], float(raw_pts)))

            for aid, pts_val in parsed:
                if pts_val is None:
                    db.execute(
                        "DELETE FROM assessment_scores WHERE assessment_id=? AND enrollment_id=?",
                        (aid, eid),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                        VALUES (?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(assessment_id, enrollment_id)
                        DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                        """,
                        (aid, eid, pts_val),
                    )

        _recalculate_subject_grades(db, subject_id)
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


ATTENDANCE_UPDATES = {
    "present": "UPDATE attendance SET present=MAX(0,present+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "late": "UPDATE attendance SET late=MAX(0,late+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "absent": "UPDATE attendance SET absent=MAX(0,absent+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
}


@app.patch("/api/attendance/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_attendance(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}
    field = payload.get("field")
    delta = payload.get("delta")
    valid_field = isinstance(field, str) and field in ATTENDANCE_UPDATES
    valid_delta = isinstance(delta, int) and not isinstance(delta, bool) and delta in (-1, 1)
    if not (valid_field and valid_delta):
        return jsonify(error="Use field present/late/absent and delta -1 or 1."), 400
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        row = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute(ATTENDANCE_UPDATES[field], (delta, row["enrollment_id"]))
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


@app.post("/api/attendance/<subject_id>/<student_id>/reset")
@login_required
@teacher_required
def reset_attendance(user, subject_id, student_id):
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        row = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute(
            "UPDATE attendance SET present=0, late=0, absent=0, updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
            (row["enrollment_id"],),
        )
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


@app.get("/api/results")
@login_required
def results(user):
    is_teacher = user["role"] == "Teacher"
    with get_db() as db:
        allowed_subjects = teacher_subject_ids(db, user) if is_teacher else None
        student_id = user["student_id"]
        if is_teacher:
            requested = request.args.get("student_id")
            if requested:
                student_id = requested
            else:
                first = db.execute(
                    """
                    SELECT e.student_id FROM enrollments e
                    JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
                    WHERE ts.teacher_id=? ORDER BY e.student_id LIMIT 1
                    """,
                    (user["teacher_id"],),
                ).fetchone()
                student_id = first["student_id"] if first else None

        rows = db.execute(
            "SELECT subject_id FROM enrollments WHERE student_id=? ORDER BY subject_id",
            (student_id,),
        ).fetchall()
        subject_ids = [r["subject_id"] for r in rows]
        if is_teacher:
            subject_ids = [s for s in subject_ids if s in allowed_subjects]

        result_rows = [record_for(db, student_id, sid) for sid in subject_ids]
        result_rows = [r for r in result_rows if r is not None]
        student = db.execute(
            "SELECT student_id, name, course FROM students WHERE student_id=?",
            (student_id,),
        ).fetchone()
        if not student or (is_teacher and not result_rows):
            return jsonify(error="Student not found."), 404

        complete_grades = [r["total_grade"] for r in result_rows if r["total_grade"] is not None]
        average = round(sum(complete_grades) / len(complete_grades), 2) if complete_grades else None
        return jsonify(
            student=dict(student),
            records=result_rows,
            general_average=average,
            is_complete=len(complete_grades) == len(result_rows) and len(result_rows) > 0,
        )


@app.errorhandler(404)
def not_found(_):
    return jsonify(error="Not found."), 404


with app.app_context():
    seed_demo_data()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=env_flag("FLASK_DEBUG"))