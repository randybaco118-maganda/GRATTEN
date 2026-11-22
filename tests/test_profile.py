"""Tests for backend/profile.py using a small stand-in app with the same auth shape as app.py."""
import io
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, session

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TEACHER, STUDENT = "teacher@olopsc.edu.ph", "2026-8812"


def world():
    os.environ["GRATTEN_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
    os.environ["GRATTEN_UPLOADS"] = tempfile.mkdtemp()
    os.environ.pop("GRATTEN_ADMIN_EMAIL", None)
    from backend import seed
    from backend.database import get_db
    from backend.profile import register
    seed.seed_demo_data()
    app = Flask(__name__)
    app.secret_key = "t"

    def login_required(fn):
        @wraps(fn)
        def w(*a, **k):
            with get_db() as db:
                row = db.execute("SELECT id,role FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
            return (jsonify(error="auth"), 401) if not row else fn(dict(row), *a, **k)
        return w

    @app.post("/test-login/<who>")
    def tl(who):
        with get_db() as db:
            session["user_id"] = db.execute("SELECT id FROM users WHERE login_id=?", (who,)).fetchone()["id"]
        return "ok"

    register(app, login_required)
    return app


def as_(app, who):
    c = app.test_client()
    c.post(f"/test-login/{who}")
    return c


def upload(c, data, name="a.png"):
    return c.post("/api/profile/avatar", data={"avatar": (io.BytesIO(data), name)}, content_type="multipart/form-data")


def test_profile_contents_and_edit_permissions():
    app = world()
    s, t = as_(app, STUDENT), as_(app, TEACHER)
    p = s.get("/api/profile").get_json()["profile"]
    assert p["student_id"] == STUDENT and p["course"] == "BSIT" and p["role"] == "Student"
    assert s.put("/api/profile", json={"name": "Hacker"}).status_code == 403          # students can't rename themselves
    assert t.put("/api/profile", json={"name": "Mrs. R. Cruz", "contact": "0917 000 0000"}).get_json()["profile"]["name"] == "Mrs. R. Cruz"
    assert t.get("/api/profile").get_json()["profile"]["contact"] == "0917 000 0000"
    assert s.put("/api/profile", json={"email": "not-an-email"}).status_code == 400
    assert s.put("/api/profile", json={"email": TEACHER}).status_code == 409           # already taken
    assert s.put("/api/profile", json={"email": "alex@olopsc.edu.ph", "contact": "x" * 41}).status_code == 400
    assert s.put("/api/profile", json={}).status_code == 400
    assert app.test_client().get("/api/profile").status_code == 401


def test_avatar_upload_validation_and_serving():
    app = world()
    s = as_(app, STUDENT)
    assert upload(s, b"just text pretending", "evil.png").status_code == 400            # wrong real type
    assert upload(s, PNG + b"0" * (2 * 1024 * 1024)).status_code == 400                 # over 2 MB
    ok = upload(s, PNG)
    assert ok.status_code == 200
    url = ok.get_json()["profile"]["avatar_url"]
    r = s.get(url)
    assert r.status_code == 200 and r.mimetype == "image/png" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert app.test_client().get(url).status_code == 401
    assert s.get("/api/avatars/..%2f..%2fapp.py").status_code == 404
    first = url.rsplit("/", 1)[1]
    second = upload(s, PNG).get_json()["profile"]["avatar_url"].rsplit("/", 1)[1]
    assert first != second and not (__import__("backend.profile", fromlist=["x"]).avatar_dir() / first).exists()
    assert s.delete("/api/profile/avatar").get_json()["profile"]["avatar_url"] is None


def test_change_password():
    app = world()
    s = as_(app, STUDENT)
    assert s.post("/api/account/password", json={"current_password": "wrong", "new_password": "NewPass123"}).status_code == 400
    assert s.post("/api/account/password", json={"current_password": "Student123!", "new_password": "short"}).status_code == 400
    assert s.post("/api/account/password", json={"current_password": "Student123!", "new_password": "Student123!"}).status_code == 400
    assert s.post("/api/account/password", json={"current_password": "Student123!", "new_password": "NewPass123"}).status_code == 200
    from werkzeug.security import check_password_hash
    from backend.database import get_db
    with get_db() as db:
        h = db.execute("SELECT password_hash FROM users WHERE login_id=?", (STUDENT,)).fetchone()[0]
    assert check_password_hash(h, "NewPass123") and "NewPass123" not in h


def test_forgot_and_reset_flow():
    app = world()
    import backend.profile as prof
    sent = []
    prof.deliver_reset_link = lambda email, link: sent.append((email, link))
    c = app.test_client()
    a = c.post("/api/auth/forgot", json={"identity": "nobody@x.ph"})
    b = c.post("/api/auth/forgot", json={"identity": TEACHER})
    assert a.status_code == b.status_code == 200 and a.get_json() == b.get_json()      # no account enumeration
    assert len(sent) == 1 and sent[0][0] == TEACHER
    token = sent[0][1].split("reset=")[1]
    from backend.database import get_db
    with get_db() as db:   # only a hash is stored
        assert db.execute("SELECT COUNT(*) FROM password_resets WHERE token_hash=?", (token,)).fetchone()[0] == 0
    assert c.post("/api/auth/reset", json={"token": token, "new_password": "short"}).status_code == 400
    assert c.post("/api/auth/reset", json={"token": "bogus", "new_password": "BrandNew123"}).status_code == 400
    assert c.post("/api/auth/reset", json={"token": token, "new_password": "BrandNew123"}).status_code == 200
    assert c.post("/api/auth/reset", json={"token": token, "new_password": "Another1234"}).status_code == 400   # reuse blocked
    from werkzeug.security import check_password_hash
    with get_db() as db:
        assert check_password_hash(db.execute("SELECT password_hash FROM users WHERE login_id=?", (TEACHER,)).fetchone()[0], "BrandNew123")
    # a newer request invalidates the older link; an expired link is rejected
    c.post("/api/auth/forgot", json={"identity": TEACHER}); c.post("/api/auth/forgot", json={"identity": TEACHER})
    old, new = (x[1].split("reset=")[1] for x in sent[1:3])
    assert c.post("/api/auth/reset", json={"token": old, "new_password": "Whatever123"}).status_code == 400
    with get_db() as db:
        db.execute("UPDATE password_resets SET expires_at=? WHERE used_at IS NULL",
                   ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    assert c.post("/api/auth/reset", json={"token": new, "new_password": "Whatever123"}).status_code == 400


def test_upgrade_from_v2_database_adds_columns_without_new_placeholders(monkeypatch=None):
    os.environ["GRATTEN_DB"] = os.path.join(tempfile.mkdtemp(), "v2.db")
    from backend import database
    database.init_db()
    with database.get_db() as db:
        db.executescript("ALTER TABLE users DROP COLUMN contact; ALTER TABLE users DROP COLUMN avatar;")
        db.execute("INSERT INTO users(login_id,password_hash,role) VALUES('n','h','Student')")
        db.execute("INSERT INTO students(student_id,user_id,name,course) VALUES('n',(SELECT id FROM users WHERE login_id='n'),'N','BSIT')")
        db.execute("PRAGMA user_version = 2"); db.commit()
    database.init_db()
    with database.get_db() as db:
        assert {"contact", "avatar"} <= {r["name"] for r in db.execute("PRAGMA table_info(users)")}
        assert db.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0     # no stray placeholder sections
