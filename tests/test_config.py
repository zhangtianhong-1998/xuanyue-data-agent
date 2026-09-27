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
        self.assertEqual(local.product_model_id, "local")
        self.assertEqual(local.base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(local.upstream_model, "local-model")
        self.assertEqual(local.api_key_env, "XUANYUE_LOCAL_API_KEY")
        self.assertEqual(
            [item.product_model_id for item in list_model_settings(self.config)],
            ["coding", "local"],
        )

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


if __name__ == "__main__":
    unittest.main()
