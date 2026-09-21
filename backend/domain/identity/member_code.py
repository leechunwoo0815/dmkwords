"""会员码（读者码）——借阅台扫码识别的孩子身份码（2026-09-21 任务包 A 批）。

**为什么不用自增 id**：id 可枚举——家长照着数字顺序试就能"扮成"别的孩子。本码由
**后端随机生成 + 校验位**，小程序只负责展示（用户拍板：禁止前端拿 id 拼码）。

形态：`M` + 8 位随机 + 1 位校验，共 **10 字符**。
- 字母表剔除易混字符（0/O、1/I/L）→ 扫码枪读得出、馆员手输也不易错；
- 10 字符的二维码版本低、Code128 条形码也窄（对比 16 字符的活动券码好扫得多）；
- 校验位 = 加权和 mod 31 映射回字母表：手输错一位会得到"码不合法"的即时提示，
  而不是让人去猜"是不是这孩没建档"。

单一来源：生成与校验都在本模块。消费点有 4 处——建孩（ChildService.create 经列默认值）、
存量回填（迁移内联同算法）、管理端按码查孩子（`circulation/router.py`）、
小程序 `children` 载荷（`identity/auth.py`）。**任何地方都不许再写一遍算法。**
"""

from __future__ import annotations

import secrets

# 31 字符（去掉 0 O 1 I L 四个易混母/数字）
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PREFIX = "M"
BODY_LEN = 8
CHECK_LEN = 1
CODE_LEN = 1 + BODY_LEN + CHECK_LEN  # 10


def _check_char(body: str) -> str:
    total = sum((i + 1) * ALPHABET.index(ch) for i, ch in enumerate(body))
    return ALPHABET[total % len(ALPHABET)]


def generate_member_code() -> str:
    """生成一个新码。随机空间 31^8 ≈ 8.5e11，配合唯一索引 `uq_child_member_code` 兜底。"""
    body = "".join(secrets.choice(ALPHABET) for _ in range(BODY_LEN))
    return f"{PREFIX}{body}{_check_char(body)}"


def normalize_member_code(raw: str | None) -> str:
    """扫码枪/手输归一化：去空白并转大写（枪尾可能带回车换行）。"""
    return (raw or "").strip().upper()


def is_valid_member_code(raw: str | None) -> bool:
    """长度 + 前缀 + 字符集 + 校验位四查；用于"码不合法"与"查无此人"分开提示。"""
    code = normalize_member_code(raw)
    if len(code) != CODE_LEN or not code.startswith(PREFIX):
        return False
    body, check = code[1 : 1 + BODY_LEN], code[-1]
    if any(ch not in ALPHABET for ch in body):
        return False
    return _check_char(body) == check
