"""本机模型设置页的目录编辑与私有凭据写入。

只接收完整的公开配置和可选新密钥；旧 .env 留在原处。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import tempfile
import tomllib
from pathlib import Path

from xuanyue.config import (
    ConfigurationError,
    _base_url,
    _catalog_from_document,
    _managed_secrets_path,
    _name,
    _read_document,
    _read_managed_secrets,
    load_api_key,
)

_PRODUCT_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_PROTOCOLS = frozenset({"openai_chat_completions"})
_LOG = logging.getLogger(__name__)
# 可选字段逐项保留；旧目录不补造显示名或模型容量。
_MODEL_OPTIONAL = frozenset(
    {
        "name",
        "max_input_tokens",
        "max_output_tokens",
        "output_token_parameter",
    }
)


class ModelConfigConflict(RuntimeError):
    """仍有会话引用将被删除的模型。"""


class ExistingModelConfigInvalid(ModelConfigConflict):
    """旧配置或私有凭据已损坏；界面不能直接覆盖以免丢失内容。"""


class ConfigSaveError(RuntimeError):
    """配置已校验，但本机文件无法安全写入。"""


def _write_private(path: Path, content: str) -> None:
    """同目录临时文件替换，绝不写入已有符号链接；POSIX 文件权限为 0600。"""
    if path.is_symlink():
        raise ConfigSaveError("local configuration file is unavailable")
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}-", dir=path.parent
        )
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError:
        raise ConfigSaveError("local configuration cannot be saved") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _public_provider(
    path: str | Path, provider_id: str, fields: dict[str, object]
) -> dict[str, object]:
    key_env = fields["api_key_env"]
    try:
        load_api_key(path, str(key_env))
        configured = True
    except (ConfigurationError, ImportError):
        configured = False
    return {
        "id": provider_id,
        "protocol": fields["protocol"],
        "base_url": fields["base_url"],
        "key_configured": configured,
        **({"name": fields["name"]} if "name" in fields else {}),
    }


def describe_model_config(path: str | Path) -> dict[str, object]:
    """给设置页的可编辑目录；不返回密钥及其环境变量名。"""
    config_path = Path(path)
    if not config_path.exists():
        return {"default_model": "", "providers": [], "models": []}
    document = _read_document(config_path)
    _catalog_from_document(document)
    providers = document["providers"]
    models = document["models"]
    assert isinstance(providers, dict) and isinstance(models, dict)
    return {
        "default_model": document["default_model"],
        "providers": [
            _public_provider(config_path, name, fields)
            for name, fields in providers.items()
        ],
        "models": [
            {
                "id": name,
                "provider": fields["provider"],
                "upstream_model": fields["upstream_model"],
                "image_input": fields.get("image_input", False),
                "reasoning_options": fields.get("reasoning_options", []),
                "default_reasoning": fields.get("default_reasoning", "default"),
                **{key: fields[key] for key in _MODEL_OPTIONAL if key in fields},
            }
            for name, fields in models.items()
        ],
    }


def _id(value: object, label: str, limit: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or not _PRODUCT_ID.fullmatch(value)
    ):
        raise ConfigurationError(f"invalid {label}")
    return value


def _credential(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ConfigurationError("invalid API key")
    return value


def _toml_string(value: str) -> str:
    # JSON 双引号字符串是当前受限字段集的合法 TOML 字符串。
    return json.dumps(value, ensure_ascii=False)


def _render_document(document: dict[str, object]) -> str:
    providers = document["providers"]
    models = document["models"]
    assert isinstance(providers, dict) and isinstance(models, dict)
    rows = [f"default_model = {_toml_string(str(document['default_model']))}", ""]
    for name, fields in providers.items():
        rows += [
            f"[providers.{name}]",
            f"protocol = {_toml_string(str(fields['protocol']))}",
            f"base_url = {_toml_string(str(fields['base_url']))}",
            f"api_key_env = {_toml_string(str(fields['api_key_env']))}",
            "",
        ]
        if "name" in fields:
            rows += [f"name = {_toml_string(fields['name'])}", ""]
    for name, fields in models.items():
        rows += [
            f"[models.{name}]",
            f"provider = {_toml_string(str(fields['provider']))}",
            f"upstream_model = {_toml_string(str(fields['upstream_model']))}",
            f"image_input = {str(fields['image_input']).lower()}",
            f"default_reasoning = {_toml_string(str(fields.get('default_reasoning', 'default')))}",
            "",
        ]
        for key in sorted(_MODEL_OPTIONAL & fields.keys()):
            value = fields[key]
            rows.append(
                f"{key} = {_toml_string(value) if isinstance(value, str) else value}"
            )
        rows.append("")
        for option in fields.get("reasoning_options", []):
            rows.append(f"[[models.{name}.reasoning_options]]")
            rows.extend(
                f"{key} = {_toml_string(value)}" for key, value in option.items()
            )
            rows.append("")
    return "\n".join(rows)


def _render_secrets(values: dict[str, str]) -> str:
    return "".join(
        f"{name}={_toml_string(secret)}\n" for name, secret in values.items()
    )


def replace_model_config(
    path: str | Path,
    value: object,
    protected_models: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """完整替换用户管理的目录；先验证全部字段，再以私有文件落盘。"""
    if not isinstance(value, dict) or set(value) != {
        "default_model",
        "providers",
        "models",
    }:
        raise ConfigurationError("invalid model configuration fields")
    provider_rows = value["providers"]
    model_rows = value["models"]
    if (
        not isinstance(provider_rows, list)
        or not provider_rows
        or not isinstance(model_rows, list)
        or not model_rows
    ):
        raise ConfigurationError("providers and models must be non-empty lists")

    config_path = Path(path)
    if config_path.is_symlink() or _managed_secrets_path(config_path).is_symlink():
        raise ConfigSaveError("local configuration file is unavailable")
    # 不覆盖用户手写但已经损坏的文件；先修复原文件再从界面修改。
    try:
        old_document = _read_document(config_path) if config_path.exists() else None
        if old_document is not None:
            _catalog_from_document(old_document)
    except ConfigurationError:
        raise ExistingModelConfigInvalid(
            "existing model configuration is invalid"
        ) from None
    old_providers = old_document["providers"] if old_document else {}
    assert isinstance(old_providers, dict)
    try:
        managed_secrets = _read_managed_secrets(config_path)
    except ConfigurationError:
        raise ExistingModelConfigInvalid(
            "existing managed credentials are invalid"
        ) from None
    added_secrets: dict[str, str] = {}
    providers: dict[str, dict[str, object]] = {}
    for row in provider_rows:
        required_provider = {"id", "protocol", "base_url"}
        if (
            not isinstance(row, dict)
            or not required_provider <= set(row)
            or set(row) - required_provider - {"api_key", "name"}
        ):
            raise ConfigurationError("invalid provider fields")
        provider_id = _id(row["id"], "provider ID", 64)
        if provider_id in providers:
            raise ConfigurationError("duplicate provider ID")
        protocol = _name(row["protocol"], "protocol")
        if protocol not in _PROTOCOLS:
            raise ConfigurationError("provider protocol is not supported")
        base_url = _base_url(row["base_url"])
        if "api_key" in row:
            key_env = f"XUANYUE_UI_KEY_{secrets.token_hex(16).upper()}"
            added_secrets[key_env] = _credential(row["api_key"])
        else:
            old = old_providers.get(provider_id)
            key_env = (
                old["api_key_env"]
                if isinstance(old, dict)
                else f"XUANYUE_UI_KEY_{secrets.token_hex(16).upper()}"
            )
        providers[provider_id] = {
            "protocol": protocol,
            "base_url": base_url,
            "api_key_env": key_env,
            **({"name": row["name"]} if "name" in row else {}),
        }

    models: dict[str, dict[str, object]] = {}
    for row in model_rows:
        required = {
            "id",
            "provider",
            "upstream_model",
            "image_input",
        }
        if (
            not isinstance(row, dict)
            or not required <= set(row)
            or set(row)
            - required
            - {"reasoning_options", "default_reasoning"}
            - _MODEL_OPTIONAL
        ):
            raise ConfigurationError("invalid model fields")
        model_id = _id(row["id"], "model ID", 128)
        if model_id in models:
            raise ConfigurationError("duplicate model ID")
        provider_id = _id(row["provider"], "model provider", 64)
        upstream = _name(row["upstream_model"], "upstream_model")
        if len(upstream) > 256 or any(ord(char) < 32 for char in upstream):
            raise ConfigurationError("invalid upstream_model")
        if type(row["image_input"]) is not bool:
            raise ConfigurationError("image_input must be a boolean")
        models[model_id] = {
            "provider": provider_id,
            "upstream_model": upstream,
            "image_input": row["image_input"],
            "reasoning_options": row.get("reasoning_options", []),
            "default_reasoning": row.get("default_reasoning", "default"),
            **{key: row[key] for key in _MODEL_OPTIONAL if key in row},
        }
    document: dict[str, object] = {
        "default_model": _id(value["default_model"], "default_model", 128),
        "providers": providers,
        "models": models,
    }
    _catalog_from_document(document)
    if protected_models - models.keys():
        raise ModelConfigConflict("a model is still used by a session")
    rendered = _render_document(document)
    # 自检输出：若序列化与输入约束未来发生偏差，原文件不受影响。
    _catalog_from_document(tomllib.loads(rendered))

    if added_secrets:
        merged = managed_secrets | added_secrets
        _write_private(_managed_secrets_path(config_path), _render_secrets(merged))
    _write_private(config_path, rendered)
    referenced = {str(fields["api_key_env"]) for fields in providers.values()}
    retained = {
        name: secret
        for name, secret in (managed_secrets | added_secrets).items()
        if name in referenced
    }
    if retained != (managed_secrets | added_secrets):
        try:
            _write_private(
                _managed_secrets_path(config_path), _render_secrets(retained)
            )
        except ConfigSaveError:
            # 目录已经提交，不把成功保存伪报成失败；下次修改仍会重试清理。
            _LOG.warning("old managed model credentials could not be removed")
    return describe_model_config(config_path)
