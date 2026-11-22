import hmac
import os
import secrets
from functools import wraps

from .config import load_env_file, env_flag

load_env_file()  # must run before anything reads os.environ

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash
from .database import get_db
from .logic import calculate_total_grade, calculate_attendance
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

# Refuse to run over HTTPS-style settings with the well-known dev secret.
if app.config["SESSION_COOKIE_SECURE"] and app.secret_key == DEFAULT_SECRET:
    raise RuntimeError("Set GRATTEN_SECRET_KEY to a long random value when COOKIE_SECURE=1.")

# --------------------------------------------------------------------------
# CSRF protection (synchronizer token kept in the signed session cookie).
# The frontend reads the token from /api/csrf (or login/me/logout responses)
# and sends it back in the X-CSRF-Token header on every POST/PUT/PATCH/DELETE.
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT u.id,u.login_id,u.email,u.role,s.student_id,s.name,s.course,"
            "t.teacher_id,t.name AS teacher_name "
            "FROM users u LEFT JOIN students s ON s.user_id=u.id "
            "LEFT JOIN teachers t ON t.user_id=u.id WHERE u.id=? AND u.active=1", (uid,)
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


# --------------------------------------------------------------------------
# Teacher-to-subject assignment
# --------------------------------------------------------------------------
def teacher_subject_ids(db, user):
    rows = db.execute("SELECT subject_id FROM teacher_subjects WHERE teacher_id=?",
                      (user["teacher_id"],)).fetchall()
    return [r["subject_id"] for r in rows]


def teacher_assigned(db, user, subject_id):
    return db.execute("SELECT 1 FROM teacher_subjects WHERE teacher_id=? AND subject_id=?",
                      (user["teacher_id"], subject_id)).fetchone() is not None


def not_assigned_response():
    return jsonify(error="You are not assigned to this subject."), 403


def record_for(db, student_id, subject_id):
    row = db.execute("""
        SELECT e.enrollment_id,e.student_id,e.subject_id,
               g.quiz,g.recitation,g.output,g.exam,
               a.present,a.late,a.absent,
               s.name AS student_name,s.course,
               sub.code,sub.name AS subject_name,sub.instructor
        FROM enrollments e
        JOIN grades g ON g.enrollment_id=e.enrollment_id
        JOIN attendance a ON a.enrollment_id=e.enrollment_id
        JOIN students s ON s.student_id=e.student_id
        JOIN subjects sub ON sub.subject_id=e.subject_id
        WHERE e.student_id=? AND e.subject_id=?
    """, (student_id, subject_id)).fetchone()
    if not row:
        return None
    data = dict(row)
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
            "SELECT id,login_id,email,password_hash,role FROM users "
            "WHERE (login_id=? OR email=?) AND active=1", (identity, identity)
        ).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify(error="Invalid ID/email or password."), 401
    session.clear()
    session["user_id"] = user["id"]
    token = new_csrf_token()  # fresh token after login (prevents session fixation of the token)
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
            rows = db.execute("""
                SELECT DISTINCT s.subject_id,s.code,s.name,s.units,s.instructor
                FROM subjects s JOIN enrollments e ON e.subject_id=s.subject_id
                WHERE e.student_id=? ORDER BY s.subject_id
            """, (user["student_id"],)).fetchall()
        else:
            rows = db.execute("""
                SELECT s.* FROM subjects s
                JOIN teacher_subjects ts ON ts.subject_id=s.subject_id
                WHERE ts.teacher_id=? ORDER BY s.subject_id
            """, (user["teacher_id"],)).fetchall()
    return jsonify(subjects=[dict(r) for r in rows])

@app.get("/api/students")
@login_required
def students(user):
    if user["role"] != "Teacher":
        return jsonify(students=[{"student_id":user["student_id"],"name":user["name"],"course":user["course"]}])
    with get_db() as db:
        rows = db.execute("""
            SELECT DISTINCT st.student_id,st.name,st.course FROM students st
            JOIN enrollments e ON e.student_id=st.student_id
            JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
            WHERE ts.teacher_id=? ORDER BY st.name
        """, (user["teacher_id"],)).fetchall()
    return jsonify(students=[dict(r) for r in rows])

@app.get("/api/records/<subject_id>")
@login_required
def records(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute("SELECT student_id FROM enrollments WHERE subject_id=? AND student_id=?",
                              (subject_id,user["student_id"])).fetchall()
        else:
            if not teacher_assigned(db, user, subject_id):
                return not_assigned_response()
            rows = db.execute("SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                              (subject_id,)).fetchall()
        output = [record_for(db, r["student_id"], subject_id) for r in rows]
    return jsonify(records=output)

@app.put("/api/grades/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_grade(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}
    fields = ("quiz","recitation","output","exam")
    if any(k not in payload for k in fields):
        return jsonify(error="Provide quiz, recitation, output, and exam."), 400
    try:
        values = [float(payload[k]) for k in fields]
    except (TypeError, ValueError):
        return jsonify(error="Grades must be numeric."), 400
    if any(v != v or v < 0 or v > 100 for v in values):  # v != v rejects NaN
        return jsonify(error="Each grade must be between 0 and 100."), 400
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        row = db.execute("SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
                         (subject_id,student_id)).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute("""UPDATE grades SET quiz=?,recitation=?,output=?,exam=?,updated_at=CURRENT_TIMESTAMP
                      WHERE enrollment_id=?""", (*values,row["enrollment_id"]))
        updated = record_for(db, student_id, subject_id)
    return jsonify(record=updated)

# Attendance updates use a fixed lookup of complete SQL statements. The client
# value only selects a key; it is never inserted into SQL text.
ATTENDANCE_UPDATES = {
    "present": "UPDATE attendance SET present=MAX(0,present+?),updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "late":    "UPDATE attendance SET late=MAX(0,late+?),updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "absent":  "UPDATE attendance SET absent=MAX(0,absent+?),updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
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
        row = db.execute("SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
                         (subject_id,student_id)).fetchone()
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
        row = db.execute("SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
                         (subject_id,student_id)).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute("UPDATE attendance SET present=0,late=0,absent=0,updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
                   (row["enrollment_id"],))
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
                first = db.execute("""
                    SELECT e.student_id FROM enrollments e
                    JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
                    WHERE ts.teacher_id=? ORDER BY e.student_id LIMIT 1
                """, (user["teacher_id"],)).fetchone()
                student_id = first["student_id"] if first else None
        rows = db.execute("SELECT subject_id FROM enrollments WHERE student_id=? ORDER BY subject_id",
                          (student_id,)).fetchall()
        subject_ids = [r["subject_id"] for r in rows]
        if is_teacher:  # teachers only see results for subjects they are assigned to
            subject_ids = [s for s in subject_ids if s in allowed_subjects]
        result_rows = [record_for(db, student_id, sid) for sid in subject_ids]
        student = db.execute("SELECT student_id,name,course FROM students WHERE student_id=?",
                             (student_id,)).fetchone()
    if not student or (is_teacher and not result_rows):
        return jsonify(error="Student not found."), 404
    average = round(sum(r["total_grade"] for r in result_rows) / len(result_rows), 2) if result_rows else 0
    return jsonify(student=dict(student), records=result_rows, general_average=average)

@app.errorhandler(404)
def not_found(_):
    return jsonify(error="Not found."), 404

with app.app_context():
    seed_demo_data()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=env_flag("FLASK_DEBUG"))
