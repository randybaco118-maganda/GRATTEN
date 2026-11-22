from backend.logic import (
    calculate_attendance,
    calculate_grade_breakdown,
    calculate_total_grade,
    validate_points,
    validate_recitation,
)


def test_weighted_grade_rounding():
    assert calculate_total_grade({"output": 95, "quiz": 92, "recitation": 88, "exam": 90}) == round(
        95 * 0.25 + 92 * 0.20 + 88 * 0.15 + 90 * 0.40
    )


def test_prompt_multi_assessment_example_equals_86():
    breakdown = calculate_grade_breakdown(
        {
            "Output": [
                {"id": 1, "name": "Output 1", "category": "Output", "max_points": 10, "earned_points": 10},
                {"id": 2, "name": "Output 2", "category": "Output", "max_points": 10, "earned_points": 7},
            ],
            "Quiz": [
                {"id": 3, "name": "Quiz 1", "category": "Quiz", "max_points": 15, "earned_points": 15},
                {"id": 4, "name": "Quiz 2", "category": "Quiz", "max_points": 15, "earned_points": 15},
            ],
            "Exam": [
                {"id": 5, "name": "Exam", "category": "Exam", "max_points": 75, "earned_points": 60},
            ],
        },
        85,
    )
    assert breakdown["components"]["output"]["percentage"] == 85.0
    assert breakdown["components"]["quiz"]["percentage"] == 100.0
    assert breakdown["components"]["recitation"]["percentage"] == 85.0
    assert breakdown["components"]["exam"]["percentage"] == 80.0
    assert breakdown["is_complete"] is True
    assert breakdown["total_grade"] == 86


def test_missing_assessment_and_empty_category_are_incomplete():
    breakdown = calculate_grade_breakdown(
        {
            "Output": [{"id": 1, "name": "Output 1", "category": "Output", "max_points": 10, "earned_points": None}],
            "Quiz": [],
            "Exam": [{"id": 2, "name": "Exam 1", "category": "Exam", "max_points": 50, "earned_points": 50}],
        },
        90,
    )
    assert breakdown["components"]["output"]["is_complete"] is False
    assert breakdown["components"]["output"]["percentage"] is None
    assert breakdown["components"]["quiz"]["is_complete"] is False
    assert breakdown["components"]["quiz"]["percentage"] is None
    assert breakdown["is_complete"] is False
    assert breakdown["total_grade"] is None


def test_validation_rules():
    assert validate_points(10, 10) == (True, None)
    assert validate_points(-1, 10)[0] is False
    assert validate_points(11, 10)[0] is False
    assert validate_points(5, 0)[0] is False
    assert validate_recitation(85) == (True, None)
    assert validate_recitation(-0.1)[0] is False
    assert validate_recitation(100.1)[0] is False


def test_three_lates_make_one_absence():
    result = calculate_attendance({"present": 10, "late": 3, "absent": 8})
    assert result["converted_absences"] == 1
    assert result["total_absences"] == 9
    assert result["is_passed"] is False


def test_eight_absences_passes():
    result = calculate_attendance({"present": 10, "late": 2, "absent": 8})
    assert result["total_absences"] == 8
    assert result["is_passed"] is True