# tests/unit/test_release_bundle_20261126.py — B0-2 发布锚点与一键回滚（2026-11-26 双审查）
"""生产目录 `/opt/dmkwords` **不是 git 工作树**（没有 .git），所以"回滚"原先只能靠人记得
上一个 commit 是哪个，再手工 rsync 一次——出事时最不可靠的东西恰好是人的记忆。

B0-2 补两个脚本：

- `scripts/release_snapshot.sh`：把**当前在跑的三块**（backend / 管理端 dist / deploy 模板）
  打成版本化包，附 MANIFEST（每包 sha256 + alembic 版本 + 当时 `/health` `/ready` 结果）；
- `scripts/release_rollback.sh`：校验 sha256 → **先把当前状态再快照一份**（回滚本身也可回滚）
  → 还原三块（dist 用 `--delete`，防止旧 chunk 残留）→ restart → 自检 `/health`。
  **不动数据库**：迁移是否降级是独立决定，脚本只把两边版本打出来对照。

判据（本文件）：
  1. 快照产出三包 + MANIFEST（含 sha256 段）+ RESTORE.md；dist 包**不含** avatars/uploads（业务数据）；
  2. 快照包被篡改 → 回滚**拒绝执行**（exit != 0，提示"已损坏"）；
  3. `--dry-run` 只打印计划、不改文件；
  4. 真回滚（`DMK_SKIP_RESTART=1` 演练模式）能把被改坏的文件还原，并用 `--delete` 清掉快照之后
     多出来的 dist 文件（否则 index.html 会引用不存在的旧 hash）；
  5. 回滚前自动留下 `pre-rollback-*` 快照（回滚也可回滚）；
  6. `--list` 能列出可用快照。

用 `DMK_ROOT` 指到临时目录跑，**不碰真实 `/opt/dmkwords`**、不碰 systemd。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SNAPSHOT = SCRIPTS / "release_snapshot.sh"
ROLLBACK = SCRIPTS / "release_rollback.sh"


def _fake_root(tmp_path: Path) -> Path:
    """造一个最小可用的"生产目录"：backend / admin-web/dist（含 avatars）/ deploy。"""
    root = tmp_path / "prod"
    (root / "backend" / "domain").mkdir(parents=True)
    (root / "backend" / "main.py").write_text("print(1)\n")
    (root / "backend" / "domain" / "svc.py").write_text("x = 1\n")
    dist = root / "admin-web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "avatars").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>\n")
    (dist / "assets" / "app-abc.js").write_text("// js\n")
    (dist / "avatars" / "p1.png").write_text("png\n")
    (root / "deploy").mkdir()
    (root / "deploy" / "start-backend.sh").write_text("#!/bin/bash\necho start\n")
    return root


def _run(script: Path, *args: str, root: Path, extra_env: dict | None = None):
    env = os.environ.copy()
    env["DMK_ROOT"] = str(root)
    env["DMK_SERVICE"] = "nonexistent-service-for-test"
    env["DMK_HEALTH_URL"] = "http://127.0.0.1:1/health"  # 必失败 → MANIFEST 记 n/a/000
    env["DMK_READY_URL"] = "http://127.0.0.1:1/ready"
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_snapshot_creates_bundle_and_manifest(tmp_path: Path):
    root = _fake_root(tmp_path)
    proc = _run(SNAPSHOT, "rel-1", root=root)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    snap = root / "backups" / "releases" / "rel-1"
    for name in ("backend.tar.gz", "admin-dist.tar.gz", "deploy.tar.gz", "MANIFEST", "RESTORE.md"):
        assert (snap / name).exists(), f"缺 {name}"

    # 快照自洽 = 回滚脚本能用它跑通（校验 sha256 → 打印还原计划）；损坏时见下一个用例
    ok = _run(ROLLBACK, "rel-1", "--dry-run", root=root)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    # 业务数据不进发布包
    listing = subprocess.run(
        ["tar", "-tzf", str(snap / "admin-dist.tar.gz")], capture_output=True, text=True, check=True
    ).stdout
    assert "avatars" not in listing, "avatars 是业务数据，不该进发布包"
    assert "index.html" in listing


def test_rollback_refuses_tampered_snapshot(tmp_path: Path):
    root = _fake_root(tmp_path)
    assert _run(SNAPSHOT, "rel-2", root=root).returncode == 0
    pkg = root / "backups" / "releases" / "rel-2" / "backend.tar.gz"
    pkg.write_bytes(pkg.read_bytes() + b"garbage")

    proc = _run(ROLLBACK, "rel-2", "--dry-run", root=root)
    assert proc.returncode != 0, "篡改过的快照必须拒绝回滚"
    assert "已损坏" in proc.stdout + proc.stderr


def test_dry_run_changes_nothing(tmp_path: Path):
    root = _fake_root(tmp_path)
    assert _run(SNAPSHOT, "rel-3", root=root).returncode == 0
    before = (root / "backend" / "main.py").read_text()
    (root / "backend" / "main.py").write_text("BROKEN\n")

    proc = _run(ROLLBACK, "rel-3", "--dry-run", root=root, extra_env={"DMK_SKIP_RESTART": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (root / "backend" / "main.py").read_text() == "BROKEN\n", (
        f"dry-run 不许改文件（快照里是 {before!r}）"
    )


def test_real_rollback_restores_files_and_prunes_dist(tmp_path: Path):
    root = _fake_root(tmp_path)
    assert _run(SNAPSHOT, "rel-4", root=root).returncode == 0

    # 模拟"部署坏了"：源文件被改、dist 换了新 chunk（旧的被删）
    (root / "backend" / "main.py").write_text("BROKEN\n")
    (root / "admin-web" / "dist" / "assets" / "app-abc.js").unlink()
    (root / "admin-web" / "dist" / "assets" / "app-xyz.js").write_text("// new\n")

    proc = _run(ROLLBACK, "rel-4", root=root, extra_env={"DMK_SKIP_RESTART": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert (root / "backend" / "main.py").read_text() == "print(1)\n", "源文件必须还原"
    assets = sorted(p.name for p in (root / "admin-web" / "dist" / "assets").iterdir())
    assert assets == ["app-abc.js"], f"dist 必须 --delete 清掉快照之后多出来的文件：{assets}"


def test_rollback_leaves_a_pre_rollback_snapshot(tmp_path: Path):
    root = _fake_root(tmp_path)
    assert _run(SNAPSHOT, "rel-5", root=root).returncode == 0
    assert _run(ROLLBACK, "rel-5", root=root, extra_env={"DMK_SKIP_RESTART": "1"}).returncode == 0

    labels = [p.name for p in (root / "backups" / "releases").iterdir() if p.is_dir()]
    assert any(x.startswith("pre-rollback-") for x in labels), labels


def test_list_shows_snapshots(tmp_path: Path):
    root = _fake_root(tmp_path)
    assert _run(SNAPSHOT, "rel-6", root=root).returncode == 0
    proc = _run(ROLLBACK, "--list", root=root)
    assert proc.returncode == 0
    assert "rel-6" in proc.stdout


def test_help_does_not_crash(tmp_path: Path):
    root = _fake_root(tmp_path)  # 只造一次（两次 mkdir 会 FileExistsError）
    for script in (SNAPSHOT, ROLLBACK):
        proc = _run(script, "--help", root=root)
        assert proc.returncode == 0
        assert "B0-2" in proc.stdout
