"""供应商管理的配置验收；所有目录、凭据与会话均为临时合成数据。"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import tempfile
import threading
import tomllib
import unittest
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

from xuanyue.chat import ChatService
from xuanyue.config import ConfigurationError, load_api_key, load_model_settings
from xuanyue.model_config_editor import describe_model_config, replace_model_config
from xuanyue.storage import LocalStore
from xuanyue.types import Event, Task

_LEGACY_CONFIG = """\
default_model = "conversation"

[providers.primary]
protocol = "openai_chat_completions"
base_url = "https://primary.example.invalid/v1"
api_key_env = "XUANYUE_PROVIDER_TEST_KEY"

[models.conversation]
provider = "primary"
upstream_model = "existing-upstream"
"""


def _proposed_config() -> dict:
    """UI 编辑请求包含凭据，读取接口不应把凭据或其环境变量名返回。"""
    return {
        "default_model": "conversation",
        "providers": [
            {
                "id": "primary",
                "name": "火山引擎（自定义）",
                "protocol": "openai_chat_completions",
                "base_url": "https://primary.example.invalid/v1",
                "api_key": "synthetic-primary-credential",
            }
        ],
        "models": [
            {
                "id": "conversation",
                "name": "经营分析助手",
                "provider": "primary",
                "upstream_model": "user-entered-endpoint",
                "image_input": True,
                "max_input_tokens": 131072,
                "max_output_tokens": 4096,
                "output_token_parameter": "max_completion_tokens",
                "reasoning_options": [{"id": "high", "label": "高", "effort": "high"}],
                "default_reasoning": "high",
            }
        ],
    }


class ProviderConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "xuanyue.toml"
        self.managed = self.root / ".env.xuanyue.models"
        # 不让开发机的环境变量影响配置测试，也不读取仓库中的真实 .env。
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _service(self) -> ChatService:
        store = LocalStore(self.root / "workspace.sqlite")
        self.addCleanup(store.close)
        return ChatService(store, self.config)

    def test_legacy_catalog_keeps_fallback_names_and_undeclared_capacities(
        self,
    ) -> None:
        self.config.write_text(_LEGACY_CONFIG, encoding="utf-8")
        settings = load_model_settings(self.config)

        self.assertEqual(settings.name, "existing-upstream")
        self.assertEqual(settings.provider_name, "primary")
        self.assertIsNone(settings.max_input_tokens)
        self.assertIsNone(settings.max_output_tokens)
        self.assertEqual(settings.output_token_parameter, "max_tokens")
        self.assertFalse(settings.image_input)
        public = describe_model_config(self.config)
        self.assertNotIn("name", public["providers"][0])
        self.assertNotIn("name", public["models"][0])
        self.assertNotIn("max_input_tokens", public["models"][0])

    def test_display_rename_keeps_session_model_and_managed_credential_binding(
        self,
    ) -> None:
        proposed = _proposed_config()
        replace_model_config(self.config, proposed)
        original = load_model_settings(self.config)
        original_credentials = self.managed.read_bytes()
        service = self._service()
        project = service.store.create_project("合成项目", str(self.root))
        session = service.create_session(project["id"], "配置验收", "agentscope")

        proposed["providers"][0].pop("api_key")
        proposed["providers"][0]["name"] = '我的供应商 "正式"'
        proposed["models"][0]["name"] = "新名称 / 图文助手"
        saved = service.save_model_configuration(proposed)
        reloaded = load_model_settings(self.config)

        self.assertEqual(saved["models"], proposed["models"])
        self.assertEqual(reloaded.name, "新名称 / 图文助手")
        self.assertEqual(reloaded.provider_name, '我的供应商 "正式"')
        self.assertEqual(reloaded.product_model_id, original.product_model_id)
        self.assertEqual(reloaded.provider_id, original.provider_id)
        self.assertEqual(reloaded.api_key_env, original.api_key_env)
        self.assertEqual(self.managed.read_bytes(), original_credentials)
        self.assertEqual(
            load_api_key(self.config, reloaded.api_key_env),
            "synthetic-primary-credential",
        )
        self.assertEqual(service.store.session(session["id"])["model"], "conversation")
        self.assertEqual(reloaded.max_input_tokens, 131072)
        self.assertEqual(reloaded.max_output_tokens, 4096)
        self.assertEqual(reloaded.output_token_parameter, "max_completion_tokens")
        self.assertEqual(reloaded.default_reasoning, "high")
        self.assertTrue(reloaded.image_input)

    def test_editing_legacy_provider_keeps_its_existing_environment_key(self) -> None:
        self.config.write_text(_LEGACY_CONFIG, encoding="utf-8")
        dotenv = self.root / ".env"
        dotenv.write_text(
            'XUANYUE_PROVIDER_TEST_KEY="synthetic-legacy-key"\n', encoding="utf-8"
        )
        original_dotenv = dotenv.read_bytes()
        proposed = _proposed_config()
        proposed["providers"][0].pop("api_key")
        replace_model_config(self.config, proposed)
        settings = load_model_settings(self.config)

        self.assertEqual(settings.api_key_env, "XUANYUE_PROVIDER_TEST_KEY")
        self.assertEqual(
            load_api_key(self.config, settings.api_key_env), "synthetic-legacy-key"
        )
        self.assertEqual(dotenv.read_bytes(), original_dotenv)
        self.assertFalse(self.managed.exists())

    def test_same_upstream_on_two_providers_keeps_distinct_service_bindings(
        self,
    ) -> None:
        proposed = _proposed_config()
        other_provider = copy.deepcopy(proposed["providers"][0])
        other_provider.update(
            {
                "id": "secondary",
                "name": "另一供应商",
                "base_url": "https://secondary.example.invalid/api/v3",
                "api_key": "synthetic-secondary-credential",
            }
        )
        other_model = copy.deepcopy(proposed["models"][0])
        other_model.update(
            {
                "id": "second-conversation",
                "provider": "secondary",
                "name": "同型号备用",
            }
        )
        proposed["providers"].append(other_provider)
        proposed["models"].append(other_model)
        replace_model_config(self.config, proposed)
        service = self._service()

        # 验证服务在构造客户端前取得的绑定；不发起任何网络请求。
        primary, primary_key = service._model_binding("conversation")
        secondary, secondary_key = service._model_binding("second-conversation")
        self.assertEqual(primary.upstream_model, secondary.upstream_model)
        self.assertEqual(primary.base_url, "https://primary.example.invalid/v1")
        self.assertEqual(secondary.base_url, "https://secondary.example.invalid/api/v3")
        self.assertEqual(primary_key, "synthetic-primary-credential")
        self.assertEqual(secondary_key, "synthetic-secondary-credential")
        self.assertNotEqual(primary.api_key_env, secondary.api_key_env)
        self.assertEqual(primary.name, "经营分析助手")
        self.assertEqual(secondary.name, "同型号备用")

    def test_public_configuration_and_catalog_expose_labels_not_credentials(
        self,
    ) -> None:
        saved = replace_model_config(self.config, _proposed_config())
        service = self._service()
        catalog = service.model_catalog()
        loaded = load_model_settings(self.config)
        wire = json.dumps(
            {"saved": saved, "config": service.model_configuration(), "models": catalog}
        )

        self.assertNotIn("synthetic-primary-credential", wire)
        self.assertNotIn(loaded.api_key_env, wire)
        self.assertNotIn('"api_key"', wire)
        self.assertNotIn('"api_key_env"', wire)
        self.assertTrue(saved["providers"][0]["key_configured"])
        self.assertTrue(catalog[0]["configured"])
        self.assertEqual(catalog[0]["name"], "经营分析助手")
        self.assertEqual(catalog[0]["provider_name"], "火山引擎（自定义）")
        self.assertEqual(catalog[0]["max_input_tokens"], 131072)
        self.assertEqual(catalog[0]["max_output_tokens"], 4096)
        self.assertEqual(catalog[0]["output_token_parameter"], "max_completion_tokens")

    def test_invalid_fields_leave_catalog_and_credentials_unchanged(self) -> None:
        replace_model_config(self.config, _proposed_config())
        original_config = self.config.read_bytes()
        original_credentials = self.managed.read_bytes()
        invalid_names = (
            None,
            "",
            " padded",
            "line\nbreak",
            "control\x7f",
            42,
            "长" * 129,
        )
        invalid_tokens = (None, False, True, 0, -1, 1.5, "4096", [], 2147483648)
        cases = [
            (section, "name", value)
            for section in ("providers", "models")
            for value in invalid_names
        ]
        cases += [
            ("models", field, value)
            for field in ("max_input_tokens", "max_output_tokens")
            for value in invalid_tokens
        ]
        cases += [
            ("models", "output_token_parameter", value)
            for value in (None, [], "max_output_tokens", "MAX_TOKENS")
        ]

        for section, field, value in cases:
            with self.subTest(section=section, field=field, value=value):
                proposed = _proposed_config()
                proposed["providers"][0]["api_key"] = (
                    "synthetic-new-secret-must-not-save"
                )
                proposed[section][0][field] = value
                with self.assertRaises(ConfigurationError):
                    replace_model_config(self.config, proposed)
                self.assertEqual(self.config.read_bytes(), original_config)
                self.assertEqual(self.managed.read_bytes(), original_credentials)

    def test_optional_capacities_can_be_removed_without_inventing_defaults(
        self,
    ) -> None:
        proposed = _proposed_config()
        replace_model_config(self.config, proposed)
        proposed["providers"][0].pop("api_key")
        for field in (
            "max_input_tokens",
            "max_output_tokens",
            "output_token_parameter",
        ):
            proposed["models"][0].pop(field)
        replace_model_config(self.config, proposed)
        settings = load_model_settings(self.config)
        saved_model = tomllib.loads(self.config.read_text())["models"]["conversation"]

        self.assertIsNone(settings.max_input_tokens)
        self.assertIsNone(settings.max_output_tokens)
        self.assertEqual(settings.output_token_parameter, "max_tokens")
        for field in (
            "max_input_tokens",
            "max_output_tokens",
            "output_token_parameter",
        ):
            self.assertNotIn(field, saved_model)

    def test_capacity_bounds_round_trip_as_integers(self) -> None:
        proposed = _proposed_config()
        proposed["models"][0].update(
            {
                "max_input_tokens": 2147483647,
                "max_output_tokens": 1,
                "output_token_parameter": "max_tokens",
            }
        )
        replace_model_config(self.config, proposed)
        settings = load_model_settings(self.config)
        self.assertEqual(settings.max_input_tokens, 2147483647)
        self.assertEqual(settings.max_output_tokens, 1)
        self.assertEqual(settings.output_token_parameter, "max_tokens")

    def test_run_freezes_display_name_before_configuration_is_renamed(self) -> None:
        proposed = _proposed_config()
        replace_model_config(self.config, proposed)
        started, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        async def response(task: Task) -> AsyncIterator[Event]:
            """暂停在运行中，验证改目录不会把本轮的名称改成新名称。"""
            yield Event(task.run_id, 1, "reply_started", {})
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("synthetic test release timed out")
            yield Event(task.run_id, 2, "text_delta", {"delta": "合成答复"})
            yield Event(
                task.run_id, 3, "reply_finished", {"finished_reason": "completed"}
            )

        store = LocalStore(self.root / "workspace.sqlite")
        self.addCleanup(store.close)
        service = ChatService(store, self.config, run_stream=response)
        project = store.create_project("合成项目", str(self.root))
        session = service.create_session(project["id"], "名称快照", "agentscope")
        execute = service._execute_thread

        def execute_and_notify(*args: object) -> None:
            try:
                execute(*args)
            finally:
                # 等运行线程彻底退出后才关闭测试数据库，避免测试制造恢复状态。
                finished.set()

        with patch.object(service, "_execute_thread", execute_and_notify):
            first_id = service.start_turn(session["id"], "第一问")
            try:
                self.assertTrue(started.wait(timeout=5))
                self.assertEqual(store.run(first_id)["model_name"], "经营分析助手")
                proposed["providers"][0].pop("api_key")
                proposed["models"][0]["name"] = "重新命名的助手"
                service.save_model_configuration(proposed)
            finally:
                release.set()
                self.assertTrue(finished.wait(timeout=5))

            finished.clear()
            second_id = service.start_turn(session["id"], "第二问")
            self.assertTrue(finished.wait(timeout=5))

        runs = service.session_detail(session["id"])["runs"]
        self.assertEqual([run["id"] for run in runs], [first_id, second_id])
        self.assertEqual(
            [run["model_name"] for run in runs], ["经营分析助手", "重新命名的助手"]
        )
        self.assertEqual([run["model"] for run in runs], ["conversation"] * 2)
        self.assertEqual([run["status"] for run in runs], ["completed"] * 2)
        self.assertEqual([run["answer"] for run in runs], ["合成答复"] * 2)

    def test_legacy_run_name_migration_keeps_history_and_can_reopen_twice(self) -> None:
        path = self.root / "legacy.sqlite"
        original = LocalStore(path)
        self.addCleanup(original.close)
        project = original.create_project("旧项目", str(self.root))
        session = original.create_session(
            project["id"], "旧会话", "langgraph", "legacy-id"
        )
        run = original.start_run(session["id"], "原问题", "legacy-id")
        original.append_event(
            run["id"], Event(run["id"], 1, "text_delta", {"delta": "原答复"})
        )
        original.finish_run(run["id"], "completed", "原答复", None)
        original.close()
        # 只在临时库中移除新列，模拟升级前的实际数据库结构和已有运行。
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE runs DROP COLUMN model_name")
            before = db.execute(
                "SELECT id,model,question,answer,status FROM runs"
            ).fetchall()

        for _ in range(2):
            reopened = LocalStore(path)
            self.addCleanup(reopened.close)
            legacy_run = reopened.run(run["id"])
            self.assertIsNone(legacy_run["model_name"])
            self.assertEqual(legacy_run["model"], "legacy-id")
            self.assertEqual(legacy_run["events"][0]["payload"], {"delta": "原答复"})
            self.assertIsNone(
                reopened.session_detail(session["id"])["runs"][0]["model_name"]
            )
            reopened.close()
            with sqlite3.connect(path) as db:
                columns = [row[1] for row in db.execute("PRAGMA table_info(runs)")]
                after = db.execute(
                    "SELECT id,model,question,answer,status FROM runs"
                ).fetchall()
            self.assertEqual(columns.count("model_name"), 1)
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
