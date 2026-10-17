"""Local repository and standard-library HTTP adapters.

The caller owns command IDs. Neither adapter retries a write with a new ID.
"""

import json
import ipaddress
import math
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TransportError(Exception):
    def __init__(self, code, message, *, status_code=None, revision=None,
                 retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.revision = revision
        self.retryable = retryable


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Bearer credentials must stay on the explicitly selected server.
        return None


class LocalTransport:
    def __init__(self, database_path="data/game.sqlite3", repository=None):
        if repository is None:
            # Import lazily so a remote-only client never imports the server.
            from server.storage import Repository
            repository = Repository(database_path)
        self.repository = repository

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self.repository, method)(*args, **kwargs)
        except Exception as exc:
            if hasattr(exc, "code") and hasattr(exc, "message"):
                raise TransportError(
                    exc.code, exc.message,
                    status_code=getattr(exc, "status_code", 400),
                    revision=getattr(exc, "revision", None),
                ) from exc
            raise

    def create_run(self, payload):
        return self._call("create_run", **payload)

    def get_view(self, run_id):
        return self._call("get_view", run_id)

    def preview(self, run_id, command):
        return self._call("preview", run_id, command)

    def options(self, run_id, command):
        return self._call("options", run_id, command)

    def write(self, run_id, operation, payload):
        method = {"commands": "submit", "reactions": "react", "retry": "retry"}[operation]
        return self._call(method, run_id, **payload)

    def events(self, run_id, after=0, limit=20):
        return self._call("events", run_id, after=after, limit=limit)

    def close(self):
        self.repository.close()


class HTTPTransport:
    def __init__(self, base_url, token, timeout=10, opener=None):
        parts = urlsplit(base_url)
        if (parts.scheme not in ("http", "https") or not parts.netloc
                or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("服务地址须为 http(s) URL，且不能含凭据、查询参数或片段。")
        try:
            port = parts.port
            if port is not None and port < 1:
                raise ValueError
        except ValueError as exc:
            raise ValueError("服务地址端口须为 1 至 65535 的整数。") from exc
        if parts.scheme == "http":
            hostname = parts.hostname.lower()
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = hostname == "localhost"
            if not loopback:
                raise ValueError("远程访问须使用 HTTPS；HTTP 仅允许 localhost、127.0.0.1 或 ::1 等回环地址。")
        if not token or not token.strip():
            raise ValueError("远程模式需要 --token 或 CMDRPG_TOKEN。")
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise ValueError("远程凭据不能包含控制字符。")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("超时必须为有限且大于 0 的秒数。")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._opener = opener or build_opener(_NoRedirect()).open

    def _request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        request = Request(
            self.base_url + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self._token,
                     "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("invalid response")
                return result
        except HTTPError as exc:
            code, message, revision = "http_error", "服务请求失败（HTTP %s）。" % exc.code, None
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                # FastAPI may wrap a structured error in detail or error.
                if isinstance(detail, dict):
                    detail = detail.get("detail", detail.get("error", detail))
                if isinstance(detail, dict):
                    code = detail.get("code", code)
                    message = detail.get("message", message)
                    revision = detail.get("current_revision", detail.get("revision"))
            except (ValueError, UnicodeError):
                pass
            # Never echo credentials, even if a reverse proxy reflects them.
            message = str(message).replace(self._token, "[凭据已隐藏]")
            raise TransportError(code, message, status_code=exc.code,
                                 revision=revision, retryable=exc.code >= 500) from exc
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise TransportError("connection_lost", "连接中断或超时，结果尚未确认。",
                                 retryable=True) from exc
        except (ValueError, UnicodeError) as exc:
            raise TransportError("invalid_response", "服务返回无法读取的 JSON，结果尚未确认。",
                                 retryable=True) from exc

    @staticmethod
    def _run_path(run_id):
        return "/runs/" + quote(str(run_id), safe="")

    def create_run(self, payload):
        return self._request("POST", "/runs", payload)

    def get_view(self, run_id):
        return self._request("GET", self._run_path(run_id) + "/view")

    def preview(self, run_id, command):
        return self._request("POST", self._run_path(run_id) + "/preview", {"command": command})

    def options(self, run_id, command):
        return self._request("POST", self._run_path(run_id) + "/options", {"command": command})

    def write(self, run_id, operation, payload):
        return self._request("POST", self._run_path(run_id) + "/" + operation, payload)

    def events(self, run_id, after=0, limit=20):
        return self._request("GET", self._run_path(run_id) + "/events?" + urlencode(
            {"after": after, "limit": limit}))

    def close(self):
        pass
