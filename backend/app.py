import os
from functools import wraps
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash
from .database import get_db, init_db
from .logic import calculate_total_grade, calculate_attendance
from .seed import seed_demo_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder="../frontend/static", static_url_path="/static")
app.secret_key = os.environ.get("GRATTEN_SECRET_KEY", "dev-only-change-this-secret")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT u.id,u.login_id,u.email,u.role,s.student_id,s.name,s.course,t.name AS teacher_name "
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
    return jsonify(user=current_user())

@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(message="Logged out.")

@app.get("/api/auth/me")
@login_required
def me(user):
    return jsonify(user=user)

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
            rows = db.execute("SELECT * FROM subjects ORDER BY subject_id").fetchall()
    return jsonify(subjects=[dict(r) for r in rows])

@app.get("/api/students")
@login_required
def students(user):
    if user["role"] != "Teacher":
        return jsonify(students=[{"student_id":user["student_id"],"name":user["name"],"course":user["course"]}])
    with get_db() as db:
        rows = db.execute("SELECT student_id,name,course FROM students ORDER BY name").fetchall()
    return jsonify(students=[dict(r) for r in rows])

@app.get("/api/records/<subject_id>")
@login_required
def records(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute("SELECT student_id FROM enrollments WHERE subject_id=? AND student_id=?",
                              (subject_id,user["student_id"])).fetchall()
        else:
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
    if any(v < 0 or v > 100 for v in values):
        return jsonify(error="Each grade must be between 0 and 100."), 400
    with get_db() as db:
        row = db.execute("SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
                         (subject_id,student_id)).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute("""UPDATE grades SET quiz=?,recitation=?,output=?,exam=?,updated_at=CURRENT_TIMESTAMP
                      WHERE enrollment_id=?""", (*values,row["enrollment_id"]))
        updated = record_for(db, student_id, subject_id)
    return jsonify(record=updated)

@app.patch("/api/attendance/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_attendance(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}
    field = payload.get("field")
    delta = payload.get("delta")
    if field not in ("present","late","absent") or delta not in (-1,1):
        return jsonify(error="Use field present/late/absent and delta -1 or 1."), 400
    with get_db() as db:
        row = db.execute("SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
                         (subject_id,student_id)).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute(f"UPDATE attendance SET {field}=MAX(0,{field}+?),updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
                   (delta,row["enrollment_id"]))
        updated = record_for(db, student_id, subject_id)
    return jsonify(record=updated)

@app.post("/api/attendance/<subject_id>/<student_id>/reset")
@login_required
@teacher_required
def reset_attendance(user, subject_id, student_id):
    with get_db() as db:
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
    student_id = user["student_id"]
    requested = request.args.get("student_id")
    if user["role"] == "Teacher":
        if requested:
            student_id = requested
        else:
            with get_db() as db:
                first_student = db.execute("SELECT student_id FROM students ORDER BY student_id LIMIT 1").fetchone()
            student_id = first_student["student_id"] if first_student else None
    with get_db() as db:
        rows = db.execute("SELECT subject_id FROM enrollments WHERE student_id=? ORDER BY subject_id",
                          (student_id,)).fetchall()
        result_rows = [record_for(db, student_id, r["subject_id"]) for r in rows]
        student = db.execute("SELECT student_id,name,course FROM students WHERE student_id=?",
                             (student_id,)).fetchone()
    if not student:
        return jsonify(error="Student not found."), 404
    average = round(sum(r["total_grade"] for r in result_rows) / len(result_rows), 2) if result_rows else 0
    return jsonify(student=dict(student), records=result_rows, general_average=average)

@app.errorhandler(404)
def not_found(_):
    return jsonify(error="Not found."), 404

with app.app_context():
    seed_demo_data()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
