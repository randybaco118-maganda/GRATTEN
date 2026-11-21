# Source review notes

Based on the uploaded 2,597-line GRATTEN HTML/JavaScript prototype:

- Seven subjects are retained: DB101, WD102, OOP103, DSA104, DM105, OS106, and NC107.
- The eleven sample students and their IDs/programs are retained.
- The DB101 base grade and attendance values are retained, including the example that has 8 recorded absences plus 3 lates (9 total, therefore FA/Fail).
- Other subject records retain the prototype's deterministic variation formula.
- The grade formula and rounding are centralized in `backend/logic.py`.
- Attendance conversion and the `<= 8` threshold are centralized in `backend/logic.py`.
- The browser's `localStorage` records are replaced by SQLite persistence through Flask endpoints.
- The prototype's role selector / quick demo access is replaced by server-validated credentials. The backend, not a front-end filter, enforces student-only access and teacher-only edits.

The uploaded source itself contains several malformed template-string fragments in visible UI code. This first milestone does not copy those broken fragments into the new frontend; it keeps the visual language and the documented rules while establishing a working, modular baseline.
