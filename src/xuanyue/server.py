"""本机开发预览用 HTTP 接口与已构建前端的静态文件入口。

仅监听 127.0.0.1；Host、Origin 与带自定义头的写入校验降低
本机网页误访风险。它不是完整的桌面宿主授权或多用户认证实现。
"""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from xuanyue.chat import ChatService, ImageInputNotSupported, ModelNotConfigured
from xuanyue.config import ConfigurationError
from xuanyue.model_config_editor import (
    ConfigSaveError,
    ExistingModelConfigInvalid,
    ModelConfigConflict,
)
from xuanyue.storage import (
    AttachmentCleanupPending,
    AttachmentInUse,
    LocalStore,
    RecordNotFound,
    SessionBusy,
    StoreInUse,
)
from xuanyue.types import MAX_IMAGES_PER_TURN

_MAX_BODY_BYTES = 1024 * 1024
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_DEV_ORIGIN = "http://127.0.0.1:5173"


def _text_field(body: dict[str, object], name: str, limit: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value.strip()


def _attachment_ids(body: dict[str, object]) -> tuple[str, ...]:
    """同轮最多四张且不能重复；项目归属由存储层原子检查。"""
    ids = body.get("attachment_ids", [])
    if (
        not isinstance(ids, list)
        or len(ids) > MAX_IMAGES_PER_TURN
        or any(not isinstance(item, str) or len(item) != 32 for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("invalid attachment IDs")
    return tuple(ids)


def make_handler(
    service: ChatService, static_dir: str | Path, port: int
) -> type[BaseHTTPRequestHandler]:
    """绑定本机服务状态；测试可复用同一 HTTP 行为而不启动命令行。"""
    assets = Path(static_dir).resolve()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{host}" for host in allowed_hosts} | {_DEV_ORIGIN}

    class Handler(BaseHTTPRequestHandler):
        server_version = "XuanyuePreview/0.1"

        def log_message(self, _format: str, *_args: object) -> None:
            # 默认 access log 会包含未经处理的 URL；本地会话不写请求日志。
            return

        def _origin(self) -> str | None:
            return self.headers.get("Origin")

        def _safe_request(self) -> bool:
            origin = self._origin()
            return self.headers.get("Host") in allowed_hosts and (
                origin is None or origin in allowed_origins
            )

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type in ("image/png", "image/jpeg"):
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if content_type == "text/html":
                # 仅约束 Python 提供的构建产物；Vite 开发页有自己的响应头。
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
                    "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'none'",
                )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if self._origin() == _DEV_ORIGIN:
                # 只为固定的本地 Vite 开发页开放跨源读取。
                self.send_header("Access-Control-Allow-Origin", _DEV_ORIGIN)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: object) -> None:
            self._send(
                status,
                json.dumps(value, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _error(self, status: int, code: str) -> None:
            self._json(status, {"error": code})

        def _path_parts(self) -> list[str]:
            return [part for part in urlsplit(self.path).path.split("/") if part]

        def do_OPTIONS(self) -> None:
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            if self._origin() != _DEV_ORIGIN:
                self._error(403, "forbidden_origin")
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", _DEV_ORIGIN)
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, X-Xuanyue-Client"
            )
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            parts = self._path_parts()
            try:
                if parts == ["api", "model-config"]:
                    self._json(200, service.model_configuration())
                elif parts == ["api", "bootstrap"]:
                    self._json(200, service.bootstrap())
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "sessions"
                ):
                    self._json(200, {"sessions": service.store.sessions(parts[2])})
                elif len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                    self._json(200, service.session_detail(parts[2]))
                elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    self._json(200, service.store.run(parts[2]))
                elif (
                    len(parts) == 5
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "attachments"
                ):
                    metadata, data = service.store.attachment(parts[2], parts[4])
                    self._send(200, data, metadata["mime_type"])
                elif parts and parts[0] == "api":
                    self._error(404, "not_found")
                else:
                    self._static()
            except RecordNotFound:
                self._error(404, "not_found")
            except ConfigurationError:
                self._error(409, "model_config_invalid")
            except Exception:  # noqa: BLE001
                # 数据库或扩展错误不回显到浏览器，避免泄露本机路径/供应商内容。
                self._error(500, "internal_error")

        def _static(self) -> None:
            path = unquote(urlsplit(self.path).path)
            target = (assets / path.lstrip("/")).resolve()
            if path == "/" or not target.is_file():
                target = assets / "index.html"
            if not target.is_relative_to(assets) or not target.is_file():
                self._error(404, "frontend_not_built")
                return
            content_type = (
                mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            )
            self._send(200, target.read_bytes(), content_type)

        def _body(self) -> dict[str, object]:
            if self.headers.get("X-Xuanyue-Client") != "desktop-dev":
                raise ValueError("missing client header")
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError("content type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise ValueError("invalid content length") from None
            if length < 1 or length > _MAX_BODY_BYTES:
                raise ValueError("invalid content length")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("invalid JSON") from None
            if not isinstance(value, dict):
                raise TypeError("JSON body must be an object")
            return value

        def _image_body(self) -> tuple[str, bytes]:
            """图片走独立的有限长度二进制入口，不进入 JSON 运行记录。"""
            if self.headers.get("X-Xuanyue-Client") != "desktop-dev":
                raise ValueError("missing client header")
            media_type = self.headers.get("Content-Type", "").strip().lower()
            if media_type not in ("image/png", "image/jpeg"):
                raise ValueError("unsupported image media type")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise ValueError("invalid content length") from None
            if length < 1 or length > _MAX_IMAGE_BYTES:
                raise ValueError("invalid image size")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete image upload")
            return media_type, data

        def do_DELETE(self) -> None:
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            if self.headers.get("X-Xuanyue-Client") != "desktop-dev":
                self._error(400, "invalid_request")
                return
            parts = self._path_parts()
            try:
                if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                    service.store.delete_project(parts[2])
                elif len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                    service.store.delete_session(parts[2])
                elif (
                    len(parts) == 5
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "attachments"
                ):
                    service.store.discard_attachment(parts[2], parts[4])
                else:
                    self._error(404, "not_found")
                    return
                self._send(204, b"", "application/json")
            except RecordNotFound:
                self._error(404, "not_found")
            except SessionBusy:
                self._error(409, "session_busy")
            except AttachmentCleanupPending:
                # 记录已删除，不能回 500 让用户误以为删除可安全重试。
                self._json(202, {"status": "deleted", "attachment_cleanup": "pending"})
            except AttachmentInUse:
                self._error(409, "attachment_in_use")
            except Exception:  # noqa: BLE001
                self._error(500, "internal_error")

        def do_PATCH(self) -> None:
            """仅开放名称更新；旧会话的内核、模型和运行记录不能被此入口改写。"""
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            try:
                parts = self._path_parts()
                if len(parts) != 3 or parts[0] != "api":
                    self._error(404, "not_found")
                    return
                if parts[1] not in ("projects", "sessions"):
                    self._error(404, "not_found")
                    return
                body = self._body()
                if parts[1] == "projects":
                    if set(body) != {"name"}:
                        raise ValueError("invalid project fields")
                    result = service.store.rename_project(
                        parts[2], _text_field(body, "name", 100)
                    )
                else:
                    if set(body) != {"title"}:
                        raise ValueError("invalid session fields")
                    result = service.store.rename_session(
                        parts[2], _text_field(body, "title", 200)
                    )
                self._json(200, result)
            except RecordNotFound:
                self._error(404, "not_found")
            except (ValueError, TypeError):
                self._error(400, "invalid_request")
            except Exception:  # noqa: BLE001
                self._error(500, "internal_error")

        def do_PUT(self) -> None:
            """设置页一次提交完整目录；密钥只在请求内出现，响应始终脱敏。"""
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            if self._path_parts() != ["api", "model-config"]:
                self._error(404, "not_found")
                return
            try:
                self._json(200, service.save_model_configuration(self._body()))
            except ExistingModelConfigInvalid:
                self._error(409, "model_config_invalid")
            except ModelConfigConflict:
                self._error(409, "model_config_conflict")
            except (ConfigurationError, ValueError, TypeError):
                self._error(400, "invalid_model_config")
            except ConfigSaveError:
                self._error(500, "model_config_save_failed")
            except Exception:  # noqa: BLE001
                self._error(500, "internal_error")

        def do_POST(self) -> None:
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            try:
                parts = self._path_parts()
                if (
                    len(parts) == 4
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "attachments"
                ):
                    media_type, data = self._image_body()
                    self._json(
                        201, service.store.save_attachment(parts[2], media_type, data)
                    )
                    return
                body = self._body()
                if parts == ["api", "projects"]:
                    if set(body) != {"name", "workspace_path"}:
                        raise ValueError("invalid project fields")
                    workspace_path = body["workspace_path"]
                    if not isinstance(workspace_path, str):
                        raise ValueError("invalid workspace path")
                    self._json(
                        201,
                        service.store.create_project(
                            _text_field(body, "name", 100), workspace_path
                        ),
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "sessions"
                ):
                    if set(body) not in (
                        {"kernel"},
                        {"kernel", "model"},
                        {"title", "kernel"},
                        {"title", "kernel", "model"},
                    ):
                        raise ValueError("invalid session fields")
                    model_id = (
                        _text_field(body, "model", 128) if "model" in body else None
                    )
                    self._json(
                        201,
                        service.create_session(
                            parts[2],
                            _text_field(body, "title", 200)
                            if "title" in body
                            else None,
                            _text_field(body, "kernel", 64),
                            model_id,
                        ),
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "sessions"]
                    and parts[3] == "turns"
                ):
                    if set(body) not in ({"text"}, {"text", "attachment_ids"}):
                        raise ValueError("invalid turn fields")
                    run_id = service.start_turn(
                        parts[2],
                        _text_field(body, "text", 20000),
                        _attachment_ids(body),
                    )
                    self._json(202, {"run_id": run_id})
                else:
                    self._error(404, "not_found")
            except RecordNotFound:
                self._error(404, "not_found")
            except SessionBusy:
                self._error(409, "session_busy")
            except ModelNotConfigured:
                self._error(503, "model_not_configured")
            except ImageInputNotSupported:
                self._error(422, "image_input_not_supported")
            except (ValueError, TypeError):
                self._error(400, "invalid_request")
            except Exception:  # noqa: BLE001
                self._error(500, "internal_error")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="启动玄月本机开发预览服务。")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--db", default="runtime/xuanyue.sqlite3")
    parser.add_argument("--config", default="xuanyue.toml")
    parser.add_argument("--static-dir", default="desktop/dist")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        store = LocalStore(args.db)
    except StoreInUse:
        print(
            "无法启动：同一数据库已被另一个玄月实例使用。"
            "请关闭旧实例后重试，或用 --db 指定不同的数据库。",
            file=sys.stderr,
        )
        return 1

    # 数据库锁先于 HTTP 端口取得；后续任何启动失败都要释放这把锁。
    try:
        service = ChatService(store, args.config)
        handler = make_handler(service, args.static_dir, args.port)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                message = (
                    f"无法启动：127.0.0.1:{args.port} 端口已被占用。"
                    "请使用已运行的服务，或关闭占用端口的进程后重试。"
                )
            else:
                message = "无法启动：本机端口绑定失败，请检查端口和系统权限后重试。"
            print(message, file=sys.stderr)
            return 1
        server.daemon_threads = True
        print(f"玄月本机预览：http://127.0.0.1:{args.port}")
        print(f"数据库：{Path(args.db).resolve()}")
        print(f"模型配置：{Path(args.config).resolve()}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
