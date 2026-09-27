"""本机开发预览用 HTTP 接口与已构建前端的静态文件入口。

仅监听 127.0.0.1；Host、Origin 与带自定义头的 JSON 写入校验降低
本机网页误访风险。它不是完整的桌面宿主授权或多用户认证实现。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from xuanyue.chat import ChatService, ModelNotConfigured
from xuanyue.storage import LocalStore, RecordNotFound, SessionBusy

_MAX_BODY_BYTES = 1024 * 1024
_DEV_ORIGIN = "http://127.0.0.1:5173"


def _text_field(body: dict[str, object], name: str, limit: int) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value.strip()


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
            if content_type == "text/html":
                # 仅约束 Python 提供的构建产物；Vite 开发页有自己的响应头。
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
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
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
                if parts == ["api", "bootstrap"]:
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
                elif parts and parts[0] == "api":
                    self._error(404, "not_found")
                else:
                    self._static()
            except RecordNotFound:
                self._error(404, "not_found")
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

        def do_POST(self) -> None:
            if not self._safe_request():
                self._error(403, "forbidden_origin")
                return
            try:
                body = self._body()
                parts = self._path_parts()
                if parts == ["api", "projects"]:
                    if set(body) != {"name"}:
                        raise ValueError("invalid project fields")
                    self._json(
                        201,
                        service.store.create_project(_text_field(body, "name", 100)),
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "projects"]
                    and parts[3] == "sessions"
                ):
                    if set(body) != {"title", "kernel"}:
                        raise ValueError("invalid session fields")
                    self._json(
                        201,
                        service.create_session(
                            parts[2],
                            _text_field(body, "title", 200),
                            _text_field(body, "kernel", 64),
                        ),
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "sessions"]
                    and parts[3] == "turns"
                ):
                    if set(body) != {"text"}:
                        raise ValueError("invalid turn fields")
                    run_id = service.start_turn(
                        parts[2], _text_field(body, "text", 20000)
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
    service = ChatService(LocalStore(args.db), args.config)
    handler = make_handler(service, args.static_dir, args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
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
        service.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
