"""Minimal authenticated client and response helpers for fee extraction."""

from __future__ import annotations

from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


WEBAPI_ENVIRONMENTS = {
    "prod": "https://raleighnc-energovapi.tylerhost.net/Apps/EnerGovWebAPI",
    "train": (
        "https://raleighnctrain-energovapi.tylerhost.net/"
        "Apps/EnerGovWebAPI"
    ),
    "test": (
        "https://raleighnctest-energovapi.tylerhost.net/"
        "Apps/EnerGovWebAPI"
    ),
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV = PROJECT_ROOT / ".env"


def _load_dotenv_file(env_file: Path | None) -> None:
    if env_file is None or not env_file.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Install dependencies from requirements.txt"
        ) from error
    load_dotenv(dotenv_path=env_file, override=False)


def load_api_credentials(env_file: Path | None) -> tuple[str, str]:
    """Load API credentials without displaying either value."""

    _load_dotenv_file(env_file)
    username = os.environ.get("ENERGOVWEBAPI_USERNAME", "").strip()
    password = os.environ.get("ENERGOVWEBAPI_PASSWORD", "")
    missing = [
        name
        for name, value in (
            ("ENERGOVWEBAPI_USERNAME", username),
            ("ENERGOVWEBAPI_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing API credential environment variable(s): "
            + ", ".join(missing)
        )
    return username, password


def case_insensitive_get(
    value: Mapping[str, object], *names: str, default: object = None
) -> object:
    lookup = {str(key).casefold(): item for key, item in value.items()}
    for name in names:
        if name.casefold() in lookup:
            return lookup[name.casefold()]
    return default


def unwrap_webapi_result(response: object) -> object:
    if not isinstance(response, Mapping):
        return response
    return case_insensitive_get(response, "Result", default=response)


def webapi_rows(
    response: object, context: str
) -> tuple[list[Mapping[str, object]], int]:
    """Validate a search envelope and return its rows and page count."""

    if not isinstance(response, Mapping):
        raise RuntimeError(f"{context} returned an unexpected response")
    success = case_insensitive_get(response, "Success", default=True)
    if success is False:
        message = case_insensitive_get(
            response,
            "ErrorMessage",
            "ValidationErrorMessage",
            default="unknown WebAPI error",
        )
        raise RuntimeError(f"{context} failed: {message}")
    result = unwrap_webapi_result(response)
    if isinstance(result, list):
        rows = result
        page_count_source = response
    elif isinstance(result, Mapping):
        rows = case_insensitive_get(result, "Result", default=[])
        page_count_source = result
    else:
        raise RuntimeError(f"{context} returned no result list")
    if not isinstance(rows, list):
        raise RuntimeError(f"{context} returned no result list")
    mapped_rows = [row for row in rows if isinstance(row, Mapping)]
    page_count = int(
        case_insensitive_get(
            page_count_source, "PageCount", default=0
        )
        or 0
    )
    return mapped_rows, page_count


def set_case_insensitive(
    payload: dict[str, object], name: str, value: object
) -> None:
    existing = {str(key).casefold(): key for key in payload}
    payload[existing.get(name.casefold(), name)] = value


class EnerGovWebApiError(RuntimeError):
    """Raised when the WebAPI rejects a request or login."""


class EnerGovWebApiClient:
    """Small cookie-authenticated client for the routes used by this repo."""

    def __init__(self, base_url: str, *, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))

    @staticmethod
    def environment_url(environment: str = "prod") -> str:
        selected = environment.strip().casefold()
        selected = {"production": "prod", "training": "train"}.get(
            selected, selected
        )
        try:
            return WEBAPI_ENVIRONMENTS[selected]
        except KeyError as error:
            choices = ", ".join(WEBAPI_ENVIRONMENTS)
            raise ValueError(
                f"Unknown WebAPI environment {environment!r}; use {choices}"
            ) from error

    @classmethod
    def from_credentials(
        cls,
        username: str,
        password: str,
        *,
        environment: str = "prod",
        timeout: float = 60,
    ) -> "EnerGovWebApiClient":
        client = cls(cls.environment_url(environment), timeout=timeout)
        try:
            client.login(username, password)
        except Exception:
            client.close()
            raise
        return client

    def __enter__(self) -> "EnerGovWebApiClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        """Retained for context-manager compatibility."""

    def _authentication_headers(self) -> dict[str, str]:
        cookies = {cookie.name: cookie.value for cookie in self._cookie_jar}
        if not cookies:
            raise EnerGovWebApiError(
                "The WebAPI session has no authentication cookie"
            )
        headers = {
            "Cookie": "; ".join(
                f"{name}={value}" for name, value in cookies.items()
            )
        }
        xsrf = cookies.get("XSRF-TOKEN")
        if xsrf:
            headers["X-XSRF-TOKEN"] = unquote(xsrf)
        current = cookies.get("tyler-energov-current-session")
        if current:
            try:
                session: Any = unquote(current)
                for _ in range(2):
                    if isinstance(session, str):
                        session = json.loads(session)
                if isinstance(session, Mapping) and session.get("sessionId"):
                    headers["egcurrentsession"] = str(session["sessionId"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return headers

    @staticmethod
    def _check_envelope(data: Mapping[str, Any], operation: str) -> None:
        if data.get("Success", data.get("success")) is False:
            message = (
                data.get("ErrorMessage")
                or data.get("errorMessage")
                or data.get("ValidationErrorMessage")
                or data.get("validationErrorMessage")
                or f"{operation} failed"
            )
            raise EnerGovWebApiError(str(message))

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | Sequence[Any] | None = None,
        *,
        query: Mapping[str, Any] | None = None,
        authenticated: bool = True,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += "?" + urlencode(query, doseq=True)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "eg-case": "camel",
            "energov-perf": "false",
        }
        if authenticated:
            headers.update(self._authentication_headers())
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url, data=body, headers=headers, method=method.upper()
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content = response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            content = error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(content)
                message = (
                    detail.get("ErrorMessage")
                    or detail.get("errorMessage")
                    or detail.get("Message")
                    or content
                )
            except (ValueError, AttributeError):
                message = content
            raise EnerGovWebApiError(
                f"{method.upper()} {path} returned HTTP {error.code}: "
                f"{str(message)[:500]}"
            ) from error
        if not content.strip():
            return None
        data = json.loads(content)
        if isinstance(data, Mapping):
            self._check_envelope(data, f"{method.upper()} {path}")
        return data

    def login(self, username: str, password: str) -> Any:
        username = username.strip()
        if not username or not password:
            raise ValueError("WebAPI username and password are required")
        try:
            result = self._request(
                "GET",
                "/api/login/login",
                query={
                    "userName": username,
                    "password": password,
                    "isCap": "false",
                    "isOutputSuppressed": "false",
                },
                authenticated=False,
            )
        except Exception:
            # Do not expose the credential-bearing login URL in a traceback.
            raise EnerGovWebApiError(
                "WebAPI credential login request failed"
            ) from None
        if not list(self._cookie_jar):
            raise EnerGovWebApiError(
                "WebAPI processed the login but issued no session cookie"
            )
        try:
            self.call("GET", "/api/identity/currentuser")
        except Exception:
            raise EnerGovWebApiError(
                "WebAPI issued cookies but could not validate the session"
            ) from None
        return result

    def call(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | Sequence[Any] | None = None,
        *,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        return self._request(method, path, payload, query=query)
