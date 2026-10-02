"""读取本机模型目录；内核只接收产品模型 ID，不接触供应商密钥。"""

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
    """一个产品模型的已校验绑定，供会话服务创建协议客户端。"""

    product_model_id: str
    protocol: str
    base_url: str
    upstream_model: str
    api_key_env: str
    image_input: bool = False


def _table(
    value: object, name: str, keys: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a table")
    unknown = set(value) - keys - (optional or set())
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


def _read_document(path: str | Path) -> dict[str, object]:
    """只解析 TOML；调用者负责决定缺失文件是否可视为空目录。"""
    try:
        with Path(path).open("rb") as source:
            return tomllib.load(source)
    except OSError:
        raise ConfigurationError("model configuration file is unavailable") from None
    except tomllib.TOMLDecodeError:
        raise ConfigurationError("model configuration is not valid TOML") from None


def _catalog_from_document(
    config: dict[str, object],
) -> tuple[str, dict[str, ModelSettings]]:
    """一次校验完整目录；供默认选择和界面列出模型共同使用。"""
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

    checked_models: dict[str, tuple[str, str, bool]] = {}
    for product_id, model in models.items():
        _name(product_id, "model ID")
        # 会话创建 API 的 model 字段上限也是 128，避免列出却无法选择的模型。
        if len(product_id) > 128:
            raise ConfigurationError("model ID is too long")
        fields = _table(
            model,
            f"model {product_id}",
            {"provider", "upstream_model"},
            {"image_input"},
        )
        provider_id = _name(fields["provider"], "model provider")
        if provider_id not in checked_providers:
            raise ConfigurationError("model refers to an unknown provider")
        image_input = fields.get("image_input", False)
        if type(image_input) is not bool:
            raise ConfigurationError("image_input must be a boolean")
        checked_models[product_id] = (
            provider_id,
            _name(fields["upstream_model"], "upstream_model"),
            image_input,
        )

    if default_model not in checked_models:
        raise ConfigurationError("default_model is not registered")
    catalog: dict[str, ModelSettings] = {}
    for product_id, (
        provider_id,
        upstream_model,
        image_input,
    ) in checked_models.items():
        protocol, base_url, api_key_env = checked_providers[provider_id]
        catalog[product_id] = ModelSettings(
            product_model_id=product_id,
            protocol=protocol,
            base_url=base_url,
            upstream_model=upstream_model,
            api_key_env=api_key_env,
            image_input=image_input,
        )
    return default_model, catalog


def _model_catalog(path: str | Path) -> tuple[str, dict[str, ModelSettings]]:
    return _catalog_from_document(_read_document(path))


def _managed_secrets_path(path: str | Path) -> Path:
    """UI 凭据独立于用户原有 .env；名字落在仓库的 .env.* 忽略规则内。"""
    config_path = Path(path)
    return config_path.parent / f".env.{config_path.stem}.models"


def _read_managed_secrets(path: str | Path) -> dict[str, str]:
    secret_path = _managed_secrets_path(path)
    if secret_path.is_symlink():
        raise ConfigurationError("managed credential file is unavailable")
    if not secret_path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        parsed = dotenv_values(secret_path, interpolate=False)
    except (OSError, ImportError):
        raise ConfigurationError("managed credential file is unavailable") from None
    if any(
        not _ENV_NAME.fullmatch(name) or value is None or "\n" in value
        for name, value in parsed.items()
    ):
        raise ConfigurationError("managed credential file is invalid")
    return {name: value for name, value in parsed.items() if value is not None}


def load_model_settings(path: str | Path, model_id: str | None = None) -> ModelSettings:
    """精确选择已登记的产品模型；显式 ID 未登记时不能回退默认模型。"""
    default_model, catalog = _model_catalog(path)
    selected = default_model if model_id is None else _name(model_id, "model ID")
    if selected not in catalog:
        raise ConfigurationError("requested model is not registered")
    return catalog[selected]


def list_model_settings(path: str | Path) -> tuple[ModelSettings, ...]:
    """按 TOML 中的登记顺序返回已校验模型；返回值含私有配置，不直接发给界面。"""
    _, catalog = _model_catalog(path)
    return tuple(catalog.values())


def load_api_key(path: str | Path, env_name: str) -> str:
    """从进程环境、UI 私有文件或原有 .env 取密钥；不从 TOML 取明文。"""
    if not _ENV_NAME.fullmatch(env_name):
        raise ConfigurationError("api_key_env must name one environment variable")
    if env_name in os.environ:
        key = os.environ[env_name]
    else:
        # python-dotenv 只在真实模型调用时需要；关闭插值以免读取其他变量。
        from dotenv import dotenv_values

        if env_name.startswith("XUANYUE_UI_KEY_"):
            key = _read_managed_secrets(path).get(env_name)
        else:
            key = dotenv_values(Path(path).parent / ".env", interpolate=False).get(
                env_name
            )
    if not key or not key.strip() or "\n" in key or "\r" in key:
        raise ConfigurationError("configured API key is unavailable")
    return key
