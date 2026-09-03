# tests/unit/test_p0b2_t19_expire_due_guard.py — P0 第二批 T19（E-10）expire_due_members 行锁+复查
"""并发红测试：无锁快照读 + 无条件 _transition(EXPIRED)——家长并发续费被旧快照
覆盖为 expired（孩子明明已续费却被标记过期）。

时序（线程模式）：
- s2 锁 Child 行（模拟续费事务进行中）
- s1 线程 expire_due_members：修复前无锁快照立即读旧 expire（已过）→ 循环
  UPDATE 阻塞在 s2 锁；修复后锁定读直接阻塞
- s2 完成续费（member_expire 推远 1 年）提交
- 修复后：s1 获锁读最新已提交 → expire 已推远 → 复查谓词不过 → 不推进（孩子仍 formal）
- 修复前：UPDATE 获锁执行无条件覆盖 → status=EXPIRED（RED）
"""

import threading
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.unit.test_wm10_concurrency import _db, _family, _h, _pay


def test_expire_due_not_override_renewed(client: TestClient, session_pair):
    h = _h(client)
    p, c, mini = _family(client, h, "13981019001", "续费竞争孩")
    _pay(client, h, c["id"], "observation_fee")  # 观察期 30 天（OBSERVATION）
    from backend.domain.identity.models import Child

    # 造到期：member_expire 推到昨天（观察期到期未续）
    with _db() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        row.member_expire = date.today() - timedelta(days=1)
        db.commit()

    s1, s2 = session_pair
    # s2：家长续费事务进行中（锁 Child 行）
    locked = s2.query(Child).filter(Child.id == c["id"]).with_for_update().first()
    assert locked is not None

    from backend.domain.identity.service import ChildService

    results = {}

    def a_expire():
        try:
            results["n"] = ChildService(s1).expire_due_members()
        except Exception as e:  # noqa: BLE001
            results["err"] = type(e).__name__

    t = threading.Thread(target=a_expire)
    t.start()
    time.sleep(1.0)  # 修复后 s1 阻塞在锁定读；修复前快照已读、UPDATE 阻塞在行锁
    locked.member_expire = date.today() + timedelta(days=365)  # 续费推远
    s2.commit()  # 续费提交，释放锁
    t.join(timeout=8)
    assert not t.is_alive(), "过期任务线程 8s 未结束（疑似死锁）"

    with _db() as db:
        row = db.query(Child).filter(Child.id == c["id"]).first()
        assert row.member_status == Child.MEMBER_OBSERVATION, (
            f"并发续费后孩子应保持 observation，实 {row.member_status}"
            "（RED=被旧快照推进 pending_evaluation/expired）"
        )
        assert row.member_expire == date.today() + timedelta(days=365), "续费到期日不应被覆盖"
