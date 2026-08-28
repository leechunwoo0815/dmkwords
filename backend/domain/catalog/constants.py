# backend/domain/catalog/constants.py — catalog 域常量（前后端人工同步）

import re

# 适读阶段下拉选项（与 admin-web/src/constants/grade.ts 保持一致）
GRADE_OPTIONS = [
    "3-4岁（幼儿园）",
    "5-6岁（幼儿园大班）",
    "7-8岁（小学低年级）",
    "9-10岁（小学中年级）",
    "11-12岁（小学高年级）",
    "13-15岁（初中）",
]

# ISBN 校验（10 位含校验位 X / 13 位）
ISBN_RE = re.compile(r"^\d{9}[\dXx]$|^\d{13}$")

# P2-8：ISBN 自动清洗——只去半角连字符与空白（含全角空格）；全角连字符（－—–）不清，
# 过度清洗会掩盖录入错误。create/update/导入三路共用。
ISBN_STRIP_RE = re.compile(r"[\s\-]")


def clean_isbn(value: str | None) -> str:
    return ISBN_STRIP_RE.sub("", value or "")
