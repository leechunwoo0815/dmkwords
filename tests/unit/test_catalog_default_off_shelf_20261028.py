# tests/unit/test_catalog_default_off_shelf_20261028.py — 新建书目默认下架（中心合同）
"""W8-D13（第十轮 B4，2026-10-28）：把「新建默认下架」从"4 个白名单模块各自守"提升为
**中心合同用例**。

背景：`tests/unit/conftest.py` 有一个 autouse 夹具 `books_on_shelf_by_default`，为了让
"建完立刻借"的老用例不必逐页 toggle，它把 `BookService.create_book` 包成"建完强制上架"，
并对 `_OFF_SHELF_CONTRACT_MODULES` 里的 4 个模块不生效。风险：**新模块**天然拿到"建书上架"
的改写，产品契约（新建默认下架 + 五检才可上架）只剩那 4 个模块在守。

本模块用**同名夹具覆盖**（pytest 的就近解析，模块级定义压过 conftest）关掉该改写，
在黑盒链路上断言默认值——它不受白名单增删影响，也不依赖任何源码文本。
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def books_on_shelf_by_default():
    """覆盖 conftest 的"建书即上架"补丁——本合同要验的正是**默认值**。"""
    yield


def test_new_book_defaults_off_shelf(client: TestClient, admin_headers: dict):
    resp = client.post(
        "/api/admin/books",
        json={
            "isbn": None,
            "title": "默认下架合同书",
            "word_count": 1000,
            "copy_count": 1,
            "author": "A",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == 0, "新建书目必须默认下架（五检通过后才可上架）"


def test_off_shelf_contract_whitelist_files_exist():
    """白名单是"夹具不生效"的逃生口——里面的模块名必须真的对应文件。"""
    from tests.unit import conftest

    assert conftest._OFF_SHELF_CONTRACT_MODULES, "白名单为空：中心合同用例也失效了"
    for name in conftest._OFF_SHELF_CONTRACT_MODULES:
        path = pathlib.Path(conftest.__file__).with_name(f"{name}.py")
        assert path.is_file(), f"{name} 在白名单但文件不存在"
