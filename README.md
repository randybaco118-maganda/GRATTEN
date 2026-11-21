# GRATTEN — Refactored Web + SQLite Starter

This is the first refactoring milestone for the supplied GRATTEN prototype. It separates the Flask backend, SQLite persistence, and browser UI while retaining the source's business rules and demo dataset.

## Preserved rules

- Final grade = `round(output * 0.25 + quiz * 0.20 + recitation * 0.15 + exam * 0.40)`.
- Every 3 lates converts to 1 equivalent absence: `floor(late / 3)`.
- Total absences = recorded absences + converted lates.
- Attendance passes at 8 or fewer total absences; more than 8 is `FA / Fail`.
- Academic result: grade below 75 is `FAILED (Academic)` unless attendance already produces `FA / FAIL (Absences)`.

## Project structure

```text
GRATTEN/
├── backend/
│   ├── app.py          # Flask routes, login/session, authorization
│   ├── database.py     # SQLite schema and connection
│   ├── logic.py        # Central grade/attendance calculations
│   └── seed.py         # Demo data based on the uploaded source
├── frontend/
│   ├── index.html
│   └── static/
│       ├── app.js
│       └── styles.css
├── instance/           # Created automatically; SQLite database lives here
├── run.py
└── requirements.txt
```

## Run locally (Windows PowerShell)

1. Install Python 3.10+.
2. Open this folder in VS Code.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

5. Start the app:

   ```powershell
   python run.py
   ```

6. Open `http://127.0.0.1:5000`.

## Demo accounts

| Role | Login ID | Password |
|---|---|---|
| Student | `2026-8812` | `Student123!` |
| Teacher | `teacher@olopsc.edu.ph` | `Teacher123!` |

These are development-only demo credentials. Change/remove them and set a strong `GRATTEN_SECRET_KEY` before deployment. The seeded student records use the sample identities from the supplied prototype.

## Database

SQLite is created automatically at `instance/gratten.db`. Open it using DB Browser for SQLite. Foreign keys are enabled on every connection.

The database stores users, students, teachers, subjects, enrollments, grade records, and attendance counters. The current prototype stores attendance as totals, so this refactor preserves those counters. A future attendance-history upgrade should add dated attendance events only after confirming the school's required attendance workflow.

## Important security and deployment notes

- Passwords are stored as Werkzeug password hashes, not plain text.
- Student record access is restricted in the API to the logged-in student's own ID.
- Grade edits and attendance changes require the Teacher role on the server.
- Do not deploy Flask's development server publicly. Use a production WSGI server and HTTPS.
- Set `GRATTEN_SECRET_KEY` to a long random secret. Set `COOKIE_SECURE=1` when served over HTTPS.
- This is a development foundation, not a production-ready school information system. Before real student data is used, add CSRF protection, account provisioning/password reset, audit logs, backups, role/subject assignment rules, privacy review, and deployment hardening.

## Current milestone limitations

- The existing visual direction (steel-blue palette, Playfair Display headings, Inter body text, responsive tables) is retained, but the UI has been simplified into a working first-pass dashboard rather than copying every original screen pixel-for-pixel.
- The source's demo "quick login" is replaced by actual password authentication.
- Teacher demo can update marks and attendance; students can view only their own records.
- Attendance still uses the original counter model, not date-by-date roll call.
