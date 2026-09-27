"""模型目录与密钥来源的本地验收；这些测试不会创建网络客户端。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xuanyue.config import (
    ConfigurationError,
    list_model_settings,
    load_api_key,
    load_model_settings,
)
from xuanyue.model_config_editor import (
    ConfigSaveError,
    ModelConfigConflict,
    describe_model_config,
    replace_model_config,
)

_CONFIG = """\
default_model = "coding"

[providers.primary]
protocol = "openai_chat_completions"
base_url = "https://api.example.invalid/v1"
api_key_env = "XUANYUE_TEST_API_KEY"

[providers.local]
protocol = "openai_chat_completions"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = "XUANYUE_LOCAL_API_KEY"

[models.coding]
provider = "primary"
upstream_model = "remote-model"

[models.local]
provider = "local"
upstream_model = "local-model"
"""


class ModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / "xuanyue.toml"
        self.config.write_text(_CONFIG, encoding="utf-8")

    def test_default_and_selected_models_keep_distinct_provider_bindings(self) -> None:
        default = load_model_settings(self.config)
        local = load_model_settings(self.config, "local")

        self.assertEqual(default.product_model_id, "coding")
        self.assertEqual(default.protocol, "openai_chat_completions")
        self.assertEqual(default.base_url, "https://api.example.invalid/v1")
        self.assertEqual(default.upstream_model, "remote-model")
        self.assertEqual(default.api_key_env, "XUANYUE_TEST_API_KEY")
        self.assertFalse(default.image_input)
        self.assertEqual(local.product_model_id, "local")
        self.assertEqual(local.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(local.upstream_model, "local-model")
        self.assertEqual(local.api_key_env, "XUANYUE_LOCAL_API_KEY")
        self.assertEqual(
            [item.product_model_id for item in list_model_settings(self.config)],
            ["coding", "local"],
        )

    def test_image_capability_is_explicit_and_boolean(self) -> None:
        enabled = _CONFIG.replace(
            'upstream_model = "local-model"',
            'upstream_model = "local-model"\nimage_input = true',
        )
        self.config.write_text(enabled, encoding="utf-8")
        self.assertTrue(load_model_settings(self.config, "local").image_input)
        self.assertFalse(load_model_settings(self.config, "coding").image_input)
        self.config.write_text(
            enabled.replace("image_input = true", 'image_input = "true"'),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigurationError):
            load_model_settings(self.config, "local")

    def test_unknown_model_or_provider_fails_instead_of_falling_back(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_model_settings(self.config, "missing")
        self.config.write_text(
            _CONFIG.replace('provider = "local"', 'provider = "missing"'),
            encoding="utf-8",
        )
        with self.assertRaises(ConfigurationError):
            load_model_settings(self.config)

    def test_catalog_does_not_list_model_ids_the_session_api_cannot_accept(
        self,
    ) -> None:
        oversized = _CONFIG.replace("[models.local]", f"[models.{'x' * 129}]")
        self.config.write_text(oversized, encoding="utf-8")
        with self.assertRaises(ConfigurationError):
            list_model_settings(self.config)

    def test_missing_unknown_and_embedded_secret_fields_fail(self) -> None:
        variants = (
            _CONFIG.replace('default_model = "coding"\n', ""),
            _CONFIG.replace('upstream_model = "remote-model"\n', ""),
            _CONFIG.replace('default_model = "coding"', 'default_model = "missing"'),
            _CONFIG.replace(
                'api_key_env = "XUANYUE_TEST_API_KEY"',
                'api_key_env = "XUANYUE_TEST_API_KEY"\napi_key = "do-not-store"',
            ),
            _CONFIG.replace(
                'api_key_env = "XUANYUE_TEST_API_KEY"',
                'api_key_env = "XUANYUE_TEST_API_KEY"\napi_key_env_typo = "X"',
            ),
        )
        for content in variants:
            with self.subTest(content=content[:35]):
                self.config.write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigurationError):
                    load_model_settings(self.config)

    def test_protocol_and_secret_reference_must_be_supported(self) -> None:
        for field, replacement in (
            ('protocol = "openai_chat_completions"', 'protocol = "claude_messages"'),
            ('api_key_env = "XUANYUE_TEST_API_KEY"', 'api_key_env = "key with spaces"'),
        ):
            with self.subTest(field=field):
                self.config.write_text(
                    _CONFIG.replace(field, replacement, 1), encoding="utf-8"
                )
                with self.assertRaises(ConfigurationError):
                    load_model_settings(self.config)

    def test_base_url_accepts_https_and_loopback_http_only(self) -> None:
        original = 'base_url = "https://api.example.invalid/v1"'
        accepted = (
            "https://another.example.invalid/api/v3",
            "http://localhost:8000/v1",
            "http://[::1]:8000/v1",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.config.write_text(
                    _CONFIG.replace(original, f'base_url = "{url}"'),
                    encoding="utf-8",
                )
                self.assertEqual(load_model_settings(self.config).base_url, url)

        rejected = (
            "http://api.example.invalid/v1",
            "http://localhost.evil.invalid/v1",
            "https://user:pass@api.example.invalid/v1",
            "https://api.example.invalid/v1?token=secret",
            "https://api.example.invalid/v1#token",
            "https://api.example.invalid:invalid/v1",
            "file:///tmp/socket",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.config.write_text(
                    _CONFIG.replace(original, f'base_url = "{url}"'),
                    encoding="utf-8",
                )
                with self.assertRaises(ConfigurationError):
                    load_model_settings(self.config)

    def test_missing_or_malformed_config_fails_without_exposing_contents(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_model_settings(self.config.with_name("missing.toml"))
        self.config.write_text('api_key = "sensitive"\n[broken', encoding="utf-8")
        with self.assertRaises(ConfigurationError) as caught:
            load_model_settings(self.config)
        self.assertNotIn("sensitive", str(caught.exception))

    def test_api_key_comes_from_environment_or_config_directory_dotenv(self) -> None:
        (self.config.parent / ".env").write_text(
            'XUANYUE_TEST_API_KEY="file-secret"\n', encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                load_api_key(self.config, "XUANYUE_TEST_API_KEY"), "file-secret"
            )
        with patch.dict(os.environ, {"XUANYUE_TEST_API_KEY": "env-secret"}):
            self.assertEqual(
                load_api_key(self.config, "XUANYUE_TEST_API_KEY"), "env-secret"
            )
        # 显式设置为空值时失败，不静默改用 .env 中的另一个密钥。
        with (
            patch.dict(os.environ, {"XUANYUE_TEST_API_KEY": ""}),
            self.assertRaises(ConfigurationError),
        ):
            load_api_key(self.config, "XUANYUE_TEST_API_KEY")

    def test_missing_or_invalid_api_key_reference_fails(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaises(ConfigurationError),
        ):
            load_api_key(self.config, "XUANYUE_TEST_API_KEY")
        with self.assertRaises(ConfigurationError):
            load_api_key(self.config, "NOT A VARIABLE")

    def test_invalid_managed_secret_file_does_not_break_original_dotenv(self) -> None:
        (self.config.parent / ".env").write_text(
            'XUANYUE_TEST_API_KEY="old-secret"\n', encoding="utf-8"
        )
        (self.config.parent / ".env.xuanyue.models").write_text(
            "broken secret syntax ###\n", encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                load_api_key(self.config, "XUANYUE_TEST_API_KEY"), "old-secret"
            )

    def test_editor_replaces_catalog_and_keeps_original_dotenv(self) -> None:
        original_dotenv = 'XUANYUE_TEST_API_KEY="old-secret"\nOTHER_VALUE=keep\n'
        dotenv = self.config.parent / ".env"
        dotenv.write_text(original_dotenv, encoding="utf-8")
        proposed = {
            "default_model": "vision",
            "providers": [
                {
                    "id": "primary",
                    "protocol": "openai_chat_completions",
                    "base_url": "https://api.example.invalid/v2",
                },
                {
                    "id": "vision-api",
                    "protocol": "openai_chat_completions",
                    "base_url": "https://vision.example.invalid/v1",
                    "api_key": "new-private-token",
                },
            ],
            "models": [
                {
                    "id": "coding",
                    "provider": "primary",
                    "upstream_model": "remote-model",
                    "image_input": False,
                },
                {
                    "id": "vision",
                    "provider": "vision-api",
                    "upstream_model": "vision-model",
                    "image_input": True,
                },
            ],
        }
        with patch.dict(os.environ, {}, clear=True):
            result = replace_model_config(self.config, proposed)
            self.assertEqual(result["default_model"], "vision")
            self.assertTrue(result["providers"][1]["key_configured"])
            self.assertNotIn("new-private-token", repr(result))
            self.assertEqual(
                load_model_settings(self.config).product_model_id, "vision"
            )
            self.assertTrue(load_model_settings(self.config).image_input)
            self.assertEqual(
                load_api_key(self.config, load_model_settings(self.config).api_key_env),
                "new-private-token",
            )
            self.assertEqual(
                load_api_key(self.config, "XUANYUE_TEST_API_KEY"), "old-secret"
            )
        self.assertEqual(dotenv.read_text(encoding="utf-8"), original_dotenv)
        self.assertNotIn("new-private-token", self.config.read_text(encoding="utf-8"))
        managed = self.config.parent / ".env.xuanyue.models"
        self.assertTrue(managed.is_file())
        self.assertEqual(managed.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

        # 第二次保存只保留仍被引用的 UI 密钥，不积累被删除的供应商。
        replace_model_config(
            self.config,
            {
                "default_model": "coding",
                "providers": [proposed["providers"][0]],
                "models": [proposed["models"][0]],
            },
        )
        self.assertNotIn("new-private-token", managed.read_text(encoding="utf-8"))
        self.assertEqual(
            describe_model_config(self.config)["models"][0]["id"], "coding"
        )

    def test_editor_rejects_invalid_inputs_before_touching_local_files(self) -> None:
        before = self.config.read_bytes()
        valid = {
            "default_model": "coding",
            "providers": [
                {
                    "id": "primary",
                    "protocol": "openai_chat_completions",
                    "base_url": "https://api.example.invalid/v1",
                }
            ],
            "models": [
                {
                    "id": "coding",
                    "provider": "primary",
                    "upstream_model": "remote-model",
                    "image_input": False,
                }
            ],
        }
        variants = (
            {**valid, "default_model": "missing"},
            {
                **valid,
                "providers": [
                    {**valid["providers"][0], "base_url": "http://example.com"}
                ],
            },
            {
                **valid,
                "providers": [{**valid["providers"][0], "protocol": "claude_messages"}],
            },
            {
                **valid,
                "providers": [{**valid["providers"][0], "api_key": "line\nsecret"}],
            },
            {**valid, "models": [{**valid["models"][0], "image_input": "yes"}]},
            {**valid, "models": [{**valid["models"][0], "id": "coding.name"}]},
        )
        for proposed in variants:
            with self.subTest(proposed=proposed):
                with self.assertRaises(ConfigurationError):
                    replace_model_config(self.config, proposed)
                self.assertEqual(self.config.read_bytes(), before)
                self.assertFalse((self.config.parent / ".env.xuanyue.models").exists())
        with self.assertRaises(ModelConfigConflict):
            replace_model_config(
                self.config,
                {
                    **valid,
                    "default_model": "another",
                    "models": [{**valid["models"][0], "id": "another"}],
                },
                frozenset({"coding"}),
            )
        self.assertEqual(self.config.read_bytes(), before)

    def test_editor_refuses_symbolic_link_configuration_target(self) -> None:
        original = self.config.read_bytes()
        target = self.config.parent / "external-target.toml"
        target.write_bytes(original)
        self.config.unlink()
        self.config.symlink_to(target)
        with self.assertRaises(ConfigSaveError):
            replace_model_config(
                self.config,
                {
                    "default_model": "coding",
                    "providers": [
                        {
                            "id": "primary",
                            "protocol": "openai_chat_completions",
                            "base_url": "https://api.example.invalid/v1",
                            "api_key": "would-be-orphaned",
                        }
                    ],
                    "models": [
                        {
                            "id": "coding",
                            "provider": "primary",
                            "upstream_model": "remote-model",
                            "image_input": False,
                        }
                    ],
                },
            )
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse((self.config.parent / ".env.xuanyue.models").exists())


if __name__ == "__main__":
    unittest.main()
