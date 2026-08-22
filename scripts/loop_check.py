#!/usr/bin/env python3
"""LOOP-2 完工校验器 —— 唯一有权宣布"闭环完成"的组件。

模式:
  --mode ledger  台账一致性校验（格式/状态词表/证据存在性/断路器纪律）
  --mode full    ledger 校验 + 现跑全量门禁 + 反假绿 + git 干净 + 终版报告存在

退出码:
  0  通过（full 模式通过时会写入 audit_loop/loop2/DONE 标记）
  1  仍有未完成项 / 校验不一致（继续干活）
  2  用法或台账解析错误
  3  剩余项全部为 BLOCKED（升级人工，见 ESCALATE.md）

设计原则: 本脚本不信任模型的任何自述，只信任文件与退出码。
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR = os.path.join(ROOT, "audit_loop", "loop2")
LEDGER = os.path.join(LOOP_DIR, "LEDGER.md")
ESCALATE = os.path.join(LOOP_DIR, "ESCALATE.md")
FINAL_REPORT = os.path.join(LOOP_DIR, "FINAL_REPORT.md")
DONE_MARKER = os.path.join(LOOP_DIR, "DONE")

STATUSES = {
    "AUDIT",
    "TODO",
    "REPRODUCED",
    "FIXED",
    "GREEN",
    "VERIFIED",
    "BLOCKED",
    "WAIVED",
}
CLOSED = {"VERIFIED", "BLOCKED", "WAIVED"}
MAX_ATTEMPTS = 3
COLS = 9  # id|phase|dimension|title|status|attempts|evidence|commit|notes


def parse_ledger() -> list[dict]:
    if not os.path.exists(LEDGER):
        print(f"[loop_check] ERROR 台账不存在: {LEDGER}", file=sys.stderr)
        sys.exit(2)
    rows = []
    errors = []
    with open(LEDGER, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not (s.startswith("|") and s.endswith("|")):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells or not re.match(r"^(A-\d+|L2-\d+)$", cells[0]):
                continue  # 表头/分隔行
            if len(cells) != COLS:
                errors.append(f"第{ln}行 列数={len(cells)}（应 {COLS}）: {cells[0]}")
                continue
            rows.append(
                dict(
                    zip(
                        [
                            "id",
                            "phase",
                            "dimension",
                            "title",
                            "status",
                            "attempts",
                            "evidence",
                            "commit",
                            "notes",
                        ],
                        cells,
                        strict=True,
                    )
                )
                | {"_line": ln}
            )
    if errors:
        print("[loop_check] ERROR 台账格式损坏，先修台账再继续:", file=sys.stderr)
        for e in errors:
            print("  - " + e, file=sys.stderr)
        sys.exit(2)
    if not rows:
        print("[loop_check] ERROR 台账没有任何条目行", file=sys.stderr)
        sys.exit(2)
    return rows


def check_ledger(rows: list[dict]) -> int:
    problems: list[str] = []
    pending: list[str] = []
    blocked: list[str] = []
    verified = waived = 0
    esc_text = ""
    if os.path.exists(ESCALATE):
        with open(ESCALATE, encoding="utf-8") as f:
            esc_text = f.read()
    for r in rows:
        rid, status = r["id"], r["status"]
        if status not in STATUSES:
            problems.append(f"{rid} 非法状态 '{status}'（词表: {'/'.join(sorted(STATUSES))}）")
            continue
        try:
            attempts = int(r["attempts"])
        except ValueError:
            problems.append(f"{rid} attempts 不是整数: '{r['attempts']}'")
            attempts = 0
        if r["commit"]:
            hres = subprocess.run(
                ["git", "cat-file", "-e", r["commit"]],
                cwd=ROOT,
                capture_output=True,
            )
            if hres.returncode != 0:
                problems.append(
                    f"{rid} commit 哈希无法解析: {r['commit']}"
                    "（禁止编造提交号，用 git log 查真实哈希）"
                )
        if status not in CLOSED:
            pending.append(f"{rid}({status})")
        if status == "VERIFIED":
            verified += 1
            if not r["evidence"]:
                problems.append(f"{rid} VERIFIED 但 evidence 为空（无证据=未验证）")
            elif not os.path.exists(os.path.join(ROOT, r["evidence"])):
                problems.append(f"{rid} evidence 文件不存在: {r['evidence']}")
            elif os.path.getsize(os.path.join(ROOT, r["evidence"])) == 0:
                problems.append(f"{rid} evidence 文件为空: {r['evidence']}")
        if status == "WAIVED":
            waived += 1
            if not r["notes"]:
                problems.append(f"{rid} WAIVED 必须在 notes 写明豁免依据（专家/用户原话）")
        if status == "BLOCKED":
            blocked.append(rid)
            if not r["notes"]:
                problems.append(f"{rid} BLOCKED 必须在 notes 写明失败原因与已试方案")
            if rid not in esc_text:
                problems.append(f"{rid} BLOCKED 但未登记进 audit_loop/loop2/ESCALATE.md")
        if attempts > MAX_ATTEMPTS and status != "BLOCKED":
            problems.append(
                f"{rid} attempts={attempts} 超过断路器上限 {MAX_ATTEMPTS}，必须转 BLOCKED"
            )
    total = len(rows)
    print(
        f"[loop_check] 台账: 共 {total} 项 | VERIFIED={verified} BLOCKED={len(blocked)} "
        f"WAIVED={waived} | 未完成={len(pending)}"
    )
    if problems:
        print("[loop_check] 校验不一致（必须修复后重跑）:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1
    if pending:
        print(
            "[loop_check] 未完成项（按台账顺序继续）: "
            + ", ".join(pending[:20])
            + (" ..." if len(pending) > 20 else "")
        )
        return 1
    if blocked:
        print(
            f"[loop_check] 剩余全为 BLOCKED（{len(blocked)} 项）: {', '.join(blocked)} —— 升级人工"
        )
        return 3
    print("[loop_check] 台账闭环：全部 VERIFIED/WAIVED")
    return 0


def run_cmd(name: str, cmd: list[str]) -> bool:
    print(f"[loop_check] 运行 {name} ...", flush=True)
    rc = subprocess.call(cmd, cwd=ROOT)
    ok = rc == 0
    print(f"[loop_check] {name} -> {'OK' if ok else f'FAIL rc={rc}'}", flush=True)
    return ok


def check_full(rows: list[dict]) -> int:
    rc = check_ledger(rows)
    if rc != 0:
        return rc
    gates_ok = run_cmd(
        "全量门禁 scripts/loop_gate.sh full", ["bash", "scripts/loop_gate.sh", "full"]
    )
    fake_ok = run_cmd(
        "反假绿 scripts/check_fake_assertions.py",
        ["venv/bin/python", "scripts/check_fake_assertions.py"],
    )
    git_rc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).returncode
    git_out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    git_ok = git_rc == 0 and git_out == ""
    if not git_ok:
        print("[loop_check] git 工作区不干净（有未提交改动）:", file=sys.stderr)
        print(git_out[:2000], file=sys.stderr)
    report_ok = os.path.exists(FINAL_REPORT) and os.path.getsize(FINAL_REPORT) > 0
    if not report_ok:
        print(
            f"[loop_check] 缺少终版报告 {os.path.relpath(FINAL_REPORT, ROOT)}"
            "（模板见 PROMPT.md §7，含基线同步自查）",
            file=sys.stderr,
        )
    if gates_ok and fake_ok and git_ok and report_ok:
        with open(DONE_MARKER, "w", encoding="utf-8") as f:
            f.write(f"DONE {datetime.now().isoformat(timespec='seconds')}\n")
        print("[loop_check] FULL PASS —— 已写入 DONE 标记，闭环完成，允许停工")
        return 0
    print("[loop_check] FULL FAIL —— 未达交付标准，禁止停工", file=sys.stderr)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="LOOP-2 完工校验器")
    ap.add_argument("--mode", choices=["ledger", "full"], default="ledger")
    args = ap.parse_args()
    rows = parse_ledger()
    if args.mode == "ledger":
        return check_ledger(rows)
    return check_full(rows)


if __name__ == "__main__":
    sys.exit(main())
