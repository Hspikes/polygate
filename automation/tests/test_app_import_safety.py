from __future__ import annotations

import importlib
import os
import unittest


class AppImportSafetyTests(unittest.TestCase):
    """回归保护：不设置任何环境变量时，import automation.app.main 不应崩溃。

    历史问题：曾经在模块顶层直接 app = create_app(_build_default_store())，
    导致缺少 AUTOMATION_REDIS_URL 时，任何 import（包括 test_api.py /
    test_contract_alignment.py）在收集阶段就 RuntimeError。改成 get_app()
    工厂模式后，import 不再触发该检查；本测试锁定这个行为。
    """

    def test_importing_main_without_env_does_not_raise(self):
        # 确保没有设置 AUTOMATION_REDIS_URL
        os.environ.pop("AUTOMATION_REDIS_URL", None)
        module = importlib.import_module("automation.app.main")
        importlib.reload(module)
        # 关键函数可用，且模块顶层没有预先构造的生产 app
        self.assertTrue(hasattr(module, "create_app"))
        self.assertTrue(hasattr(module, "get_app"))
        self.assertTrue(hasattr(module, "_compile_preview"))

    def test_create_app_with_inmemory_store_works_without_redis(self):
        os.environ.pop("AUTOMATION_REDIS_URL", None)
        from automation.app.main import create_app
        from automation.app.store import InMemoryAutomationStore
        from fastapi.testclient import TestClient

        client = TestClient(create_app(InMemoryAutomationStore()))
        self.assertEqual(client.get("/health").status_code, 200)

    def test_get_app_still_fails_fast_when_redis_url_missing(self):
        # 生产保护必须保留：真正调用 get_app() 且缺 AUTOMATION_REDIS_URL 时应明确失败
        os.environ.pop("AUTOMATION_REDIS_URL", None)
        from automation.app.main import get_app

        with self.assertRaises(RuntimeError):
            get_app()


if __name__ == "__main__":
    unittest.main(verbosity=2)