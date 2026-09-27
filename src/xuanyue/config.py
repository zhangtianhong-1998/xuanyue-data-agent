"""读取 CLI 模型配置；内核只接收产品模型 ID，不接触供应商密钥。"""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PROTOCOLS = frozenset({"openai_chat_completions"})


class ConfigurationError(ValueError):
    """配置缺失或不受支持；错误消息不包含文件内容和凭据。"""


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """一个产品模型的已校验绑定，供 CLI 创建对应协议客户端。"""

    product_model_id: str
    protocol: str
    base_url: str
    upstream_model: str
    api_key_env: str


def _table(value: object, name: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a table")
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown or missing:
        raise ConfigurationError(f"{name} has unknown or missing fields")
    return value


def _name(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationError(f"{name} must be a non-empty name")
    return value


def _base_url(value: object) -> str:
    """真实服务必须使用 HTTPS；仅本机回环地址可用于 HTTP 测试。"""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationError("base_url must be a non-empty URL")
    if any(ord(char) < 32 for char in value):
        raise ConfigurationError("base_url contains a control character")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        _ = parsed.port  # 触发无效端口检查，避免交给 SDK 后才失败。
    except ValueError:
        raise ConfigurationError("base_url is invalid") from None
    if (
        not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError("base_url contains unsupported URL components")
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if loopback:
            return value
    raise ConfigurationError("base_url requires HTTPS or loopback HTTP")


def load_model_settings(path: str | Path, model_id: str | None = None) -> ModelSettings:
    """解析模型目录并精确选择产品模型；不读取或推断任何密钥。"""
    try:
        with Path(path).open("rb") as source:
            config = tomllib.load(source)
    except OSError:
        raise ConfigurationError("model configuration file is unavailable") from None
    except tomllib.TOMLDecodeError:
        raise ConfigurationError("model configuration is not valid TOML") from None

    root = _table(config, "configuration", {"default_model", "providers", "models"})
    default_model = _name(root["default_model"], "default_model")
    providers = root["providers"]
    models = root["models"]
    if not isinstance(providers, dict) or not providers:
        raise ConfigurationError("providers must be a non-empty table")
    if not isinstance(models, dict) or not models:
        raise ConfigurationError("models must be a non-empty table")

    checked_providers: dict[str, tuple[str, str, str]] = {}
    for provider_id, provider in providers.items():
        _name(provider_id, "provider ID")
        fields = _table(
            provider,
            f"provider {provider_id}",
            {"protocol", "base_url", "api_key_env"},
        )
        protocol = _name(fields["protocol"], "protocol")
        if protocol not in _PROTOCOLS:
            raise ConfigurationError("provider protocol is not supported")
        key_env = _name(fields["api_key_env"], "api_key_env")
        if not _ENV_NAME.fullmatch(key_env):
            raise ConfigurationError("api_key_env must name one environment variable")
        checked_providers[provider_id] = (
            protocol,
            _base_url(fields["base_url"]),
            key_env,
        )

    checked_models: dict[str, tuple[str, str]] = {}
    for product_id, model in models.items():
        _name(product_id, "model ID")
        fields = _table(model, f"model {product_id}", {"provider", "upstream_model"})
        provider_id = _name(fields["provider"], "model provider")
        if provider_id not in checked_providers:
            raise ConfigurationError("model refers to an unknown provider")
        checked_models[product_id] = (
            provider_id,
            _name(fields["upstream_model"], "upstream_model"),
        )

    if default_model not in checked_models:
        raise ConfigurationError("default_model is not registered")
    selected = default_model if model_id is None else _name(model_id, "model ID")
    if selected not in checked_models:
        raise ConfigurationError("requested model is not registered")
    provider_id, upstream_model = checked_models[selected]
    protocol, base_url, api_key_env = checked_providers[provider_id]
    return ModelSettings(
        product_model_id=selected,
        protocol=protocol,
        base_url=base_url,
        upstream_model=upstream_model,
        api_key_env=api_key_env,
    )


def load_api_key(path: str | Path, env_name: str) -> str:
    """从进程环境或配置同目录的 .env 取密钥；不从 TOML 取明文密钥。"""
    if not _ENV_NAME.fullmatch(env_name):
        raise ConfigurationError("api_key_env must name one environment variable")
    if env_name in os.environ:
        key = os.environ[env_name]
    else:
        # python-dotenv 只在真实模型调用时需要；关闭插值以免读取其他变量。
        from dotenv import dotenv_values

        key = dotenv_values(Path(path).parent / ".env", interpolate=False).get(env_name)
    if not key or not key.strip() or "\n" in key or "\r" in key:
        raise ConfigurationError("configured API key is unavailable")
    return key
