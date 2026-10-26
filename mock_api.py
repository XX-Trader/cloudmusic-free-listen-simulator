"""一键接口形态的离线模拟服务；仅监听回环地址，不接触任何生产凭证。"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import simulator


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8848
MAX_BODY_BYTES = 4096
RUN_PATH = "/v1/demo/run"
HEALTH_PATH = "/v1/demo/health"
API_VERSION = "v1"


class ApiError(Exception):
    """描述一个可以直接映射为 HTTP 状态的接口错误。"""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class DemoApiHandler(BaseHTTPRequestHandler):
    """提供一键运行与健康检查两个只读入口。"""

    server_version = "FreeListenMock/" + API_VERSION
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        """抑制默认访问日志；接口不记录请求头与请求体。"""
        return

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_payload(self, error: ApiError) -> None:
        self._send_json(
            error.status,
            {
                "mode": "offline_demo",
                "real_entitlement_granted": False,
                "error": error.message,
            },
        )

    def _read_json_body(self) -> dict[str, object]:
        declared = self.headers.get("Content-Length")
        if declared is None:
            return {}
        try:
            size = int(declared)
        except ValueError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Content-Length 非法") from error
        if size < 0 or size > MAX_BODY_BYTES:
            raise ApiError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"请求体不得超过 {MAX_BODY_BYTES} 字节",
            )
        if size == 0:
            return {}
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 UTF-8 JSON") from error
        if not isinstance(payload, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:
        """只允许健康检查；已知的写入口返回 405，其他路径返回 404。"""
        if self.path == RUN_PATH:
            self._send_error_payload(
                ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "该入口只接受 POST")
            )
            return
        if self.path != HEALTH_PATH:
            self._send_error_payload(ApiError(HTTPStatus.NOT_FOUND, "未知路径"))
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "mode": "offline_demo",
                "real_entitlement_granted": False,
                "network_requests": 0,
                "loopback_only": True,
                "bind": f"{LOOPBACK_HOST}:{self.server.server_address[1]}",
                "scenarios": sorted(simulator.SCENARIOS),
                "endpoint": RUN_PATH,
            },
        )

    def do_POST(self) -> None:
        """只允许一键运行；场景名必须来自注册表。"""
        if self.path != RUN_PATH:
            self._send_error_payload(ApiError(HTTPStatus.NOT_FOUND, "未知路径"))
            return
        try:
            payload = self._read_json_body()
            scenario = payload.get("scenario", "success")
            if not isinstance(scenario, str) or scenario not in simulator.SCENARIOS:
                raise ApiError(
                    HTTPStatus.BAD_REQUEST,
                    "未知场景；可选：" + ", ".join(sorted(simulator.SCENARIOS)),
                )
            result = simulator.simulate(scenario)
        except ApiError as error:
            self._send_error_payload(error)
            return
        result["api"] = {
            "version": API_VERSION,
            "endpoint": RUN_PATH,
            "loopback_only": True,
        }
        self._send_json(HTTPStatus.OK, result)

    def do_PUT(self) -> None:
        """明确拒绝其他写方法。"""
        self._send_error_payload(ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "不支持的方法"))

    do_DELETE = do_PUT
    do_PATCH = do_PUT


def create_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """创建仅绑定回环地址的服务；端口为 0 时由系统分配；返回可关闭的服务实例。"""
    return ThreadingHTTPServer((LOOPBACK_HOST, port), DemoApiHandler)


def main() -> int:
    """启动一键 Mock 接口；无参数；按键中断时返回 0。"""
    parser = argparse.ArgumentParser(
        description="免费听离线模拟的一键接口，仅监听 127.0.0.1，不发放真实权益"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    arguments = parser.parse_args()

    server = create_server(arguments.port)
    host, port = server.server_address[0], server.server_address[1]
    print(
        json.dumps(
            {
                "mode": "offline_demo",
                "loopback_only": True,
                "bind": f"{host}:{port}",
                "health": HEALTH_PATH,
                "run": RUN_PATH,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
