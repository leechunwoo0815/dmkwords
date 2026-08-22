"""题目答案校验 — F-071/F-088/F-093 三入口统一规则

create（advancement）/ bulk（admin）/ update（advancement+admin）三处题目写入共用，
防止"同类调用点漏改"：correct_answer 必须 A-D，且指向的选项非空。
"""

QUESTION_OPTIONS = ("A", "B", "C", "D")


def validate_question_correct_answer(
    correct_answer: str,
    option_a: str | None,
    option_b: str | None,
    option_c: str | None,
    option_d: str | None,
) -> None:
    """校验 correct_answer 合法且指向选项非空；非法抛 ValidationError（422）"""
    from backend.common.exceptions import ValidationError

    if correct_answer not in QUESTION_OPTIONS:
        raise ValidationError(f"correct_answer 必须为 A/B/C/D，当前值: {correct_answer!r}")
    option_map = {
        "A": option_a,
        "B": option_b,
        "C": option_c,
        "D": option_d,
    }
    if not str(option_map[correct_answer] or "").strip():
        raise ValidationError(f"correct_answer={correct_answer} 指向的选项不能为空")
