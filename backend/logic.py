"""Authoritative GRATTEN grading and attendance rules."""
import math

WEIGHTS = {"output": 0.25, "quiz": 0.20, "recitation": 0.15, "exam": 0.40}


def is_valid_number(value):
    """True for finite ints/floats (or numeric strings); False for bool, NaN, inf, junk."""
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def validate_points(earned, max_points):
    """Return (True, None) or (False, message)."""
    if not is_valid_number(earned) or not is_valid_number(max_points):
        return False, "Scores must be numeric."
    earned, max_points = float(earned), float(max_points)
    if max_points <= 0:
        return False, "Maximum points must be greater than zero."
    if earned < 0 or earned > max_points:
        return False, f"Earned points must be between 0 and {max_points:g}."
    return True, None


def validate_recitation(score):
    if not is_valid_number(score):
        return False, "Recitation must be numeric."
    if not 0 <= float(score) <= 100:
        return False, "Recitation must be between 0 and 100."
    return True, None


def _component(name, items):
    """items: dicts with max_points and earned_points (None = not yet graded).
    A component is complete only if it has assessments and every one is scored,
    so a missing score is never treated as perfect (or as zero)."""
    graded = [i for i in items if i.get("earned_points") is not None]
    complete = bool(items) and len(graded) == len(items)
    earned = float(sum(i["earned_points"] for i in graded))
    possible = float(sum(i["max_points"] for i in graded))
    pct = round(earned / possible * 100, 2) if complete and possible > 0 else None
    return {
        "earned": earned, "possible": possible, "percentage": pct,
        "earned_points": earned, "max_points": possible,
        "assessments": [{"assessment_id": i.get("id"), "name": i.get("name"),
                         "earned_points": i.get("earned_points"), "max_points": i.get("max_points")}
                        for i in items],
        "is_complete": pct is not None, "graded": len(graded), "total": len(items),
        "status": "complete" if pct is not None else ("partial" if graded else "not_graded"),
        "weight": WEIGHTS[name],
        "contribution": None if pct is None else round(pct * WEIGHTS[name], 2),
    }


def calculate_grade_breakdown(grouped, recitation):
    """grouped: {"Output": [...], "Quiz": [...], "Exam": [...]}; recitation: 0-100 or None.
    total_grade is None unless all four components are complete."""
    comps = {
        "output": _component("output", grouped.get("Output", [])),
        "quiz": _component("quiz", grouped.get("Quiz", [])),
        "exam": _component("exam", grouped.get("Exam", [])),
    }
    rec_ok = recitation is not None and validate_recitation(recitation)[0]
    rec = float(recitation) if rec_ok else None
    comps["recitation"] = {
        "earned": rec or 0.0, "possible": 100.0 if rec_ok else 0.0, "percentage": rec,
        "earned_points": rec or 0.0, "max_points": 100.0, "assessments": [],
        "is_complete": rec_ok, "graded": int(rec_ok), "total": 1,
        "status": "complete" if rec_ok else "not_graded",
        "weight": WEIGHTS["recitation"],
        "contribution": None if rec is None else round(rec * WEIGHTS["recitation"], 2),
    }
    complete = all(c["is_complete"] for c in comps.values())
    total = None
    if complete:
        total = round(sum(comps[k]["percentage"] * WEIGHTS[k] for k in WEIGHTS))
    return {"components": comps, "is_complete": complete, "total_grade": total}


def calculate_total_grade(record):
    """Weighted final grade rounded to an integer; None if any component is missing."""
    if any(record.get(k) is None for k in WEIGHTS):
        return None
    return round(sum(float(record[k]) * w for k, w in WEIGHTS.items()))


def calculate_attendance(record):
    converted_absences = int(record["late"]) // 3
    total_absences = int(record["absent"]) + converted_absences
    passed = total_absences <= 8
    return {
        "present": int(record["present"]),
        "late": int(record["late"]),
        "raw_absent": int(record["absent"]),
        "converted_absences": converted_absences,
        "total_absences": total_absences,
        "is_passed": passed,
        "status_label": "Attendance Passed" if passed else "FA / Fail",
    }
