"""One adaptive attempt gate and policy refresher per asynchronous client."""
import asyncio
import copy
import json
import math
import re
import time
from contextlib import suppress

import httpx
import jsonschema

from .dispatcher import DispatcherClient
from .protocol import safe_to_retry, validate_operation, validate_origin


class ProtocolError(ValueError):
    """A discovery or configuration contract was not satisfied."""


class OperationError(Exception):
    """An operation failed; attributes contain no credentials or input values."""

    def __init__(self, code, execution, *, status=None, attempts=0):
        self.code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "http_error"
        self.execution = execution
        self.status = status
        self.attempts = attempts
        super().__init__(f"{self.code} (execution={execution}, status={status}, attempts={attempts})")


class DeadlineExceeded(OperationError):
    """The logical task's original deadline expired."""


def validate_policy(policy, previous=None):
    """Validate the adaptive v3 contract and continuity of one gateway identity."""
    if not isinstance(policy, dict):
        raise ProtocolError("Invalid policy object")
    limits = policy.get("limits", {})
    integer = lambda value: type(value) is int
    if (
        policy.get("version") != 3 or not integer(policy.get("version"))
        or not isinstance(policy.get("principal_id"), str) or not policy["principal_id"]
        or policy.get("client_class") not in ("agent", "interactive")
        or not isinstance(limits, dict) or limits.get("scope") != "principal"
        or not integer(limits.get("max_outstanding")) or not 1 <= limits["max_outstanding"] <= 1000
        or not integer(policy.get("policy_revision")) or policy["policy_revision"] < 1
        or not integer(policy.get("refresh_after_ms")) or not 100 <= policy["refresh_after_ms"] <= 60000
        or not integer(policy.get("outstanding")) or policy["outstanding"] < 0
        or not isinstance(policy.get("operations"), list)
        or ("service_id" in policy and (not isinstance(policy["service_id"], str) or not policy["service_id"]))
    ):
        raise ProtocolError("Invalid or unsupported adaptive policy")
    names = set()
    for operation in policy["operations"]:
        try:
            validate_operation(operation)
        except (ValueError, jsonschema.SchemaError):
            raise ProtocolError("Invalid operation descriptor") from None
        if operation["name"] in names:
            raise ProtocolError("Duplicate operation name")
        names.add(operation["name"])
    if previous and (
        any(policy.get(key) != previous.get(key) for key in ("principal_id", "client_class", "service_id"))
        or policy["policy_revision"] < previous["policy_revision"]
        or (policy["policy_revision"] == previous["policy_revision"]
            and limits["max_outstanding"] != previous["limits"]["max_outstanding"])
    ):
        raise ProtocolError("Policy identity or revision changed inconsistently")
    return copy.deepcopy(policy)


class AsyncClient:
    """An adaptive v3 client. Use `async with` and await all calls before closing.

    The gate is process-local, not a reservation of server budget. Use a shared
    dispatcher to coordinate separate local processes. Transport loss, cancellation
    and execution timeouts never authorize replay of an unknown outcome.
    """

    def __init__(self, origin, token, *, dispatcher_socket=None, transport=None,
                 max_response_bytes=16 * 1024 * 1024):
        try:
            origin = validate_origin(origin)
        except ValueError as error:
            raise ProtocolError(str(error)) from None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
            raise ProtocolError("Invalid bearer credential")
        if type(max_response_bytes) is not int or max_response_bytes < 1:
            raise ProtocolError("max_response_bytes must be a positive integer")
        self._origin = origin.rstrip("/")
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
            trust_env=False, follow_redirects=False, transport=transport)
        self._dispatcher = DispatcherClient(dispatcher_socket, token) if dispatcher_socket else None
        self._maximum_body = max_response_bytes
        self._condition = asyncio.Condition()
        self._policy = None
        self._active = 0
        self._ready = False
        self._closed = False
        self._refresher = None

    @property
    def policy(self):
        """A detached full discovery snapshot, or None before initial discovery."""
        return copy.deepcopy(self._policy)

    @property
    def ready(self):
        """Whether new attempts may use the last successfully refreshed policy."""
        return self._ready and not self._closed

    @property
    def active_attempts(self):
        """HTTP attempts currently held by this client, excluding dispatcher jobs."""
        return self._active

    async def __aenter__(self):
        if self._closed or self._refresher is not None:
            raise ProtocolError("Client cannot be reopened or entered twice")
        try:
            await self.refresh()
        except BaseException:
            await self.aclose()
            raise
        self._refresher = asyncio.create_task(self._poll(), name="ai-protocol-policy")
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def _request(self, method, path, *, params=None, maximum=None):
        body = bytearray()
        async with self._http.stream(method, self._origin + path, json=params, timeout=30) as response:
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > (maximum or self._maximum_body):
                    raise ProtocolError("Response exceeds the configured size limit")
                body.extend(chunk)
            headers = dict(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(response.status_code, headers=headers,
                                  content=bytes(body), request=response.request)

    async def refresh(self):
        """Fetch and apply a coherent policy, pausing new work on any failure."""
        async with self._condition:
            if self._closed:
                raise ProtocolError("Client is closed")
        try:
            async with asyncio.timeout(2):
                response = await self._request("GET", "/agent-policy", maximum=1024 * 1024)
                response.raise_for_status()
                policy = response.json()
                async with self._condition:
                    validated = validate_policy(policy, self._policy)
                    if not self._closed:
                        self._policy, self._ready = validated, True
                        self._condition.notify_all()
            return self.policy
        except (httpx.HTTPError, ValueError, TimeoutError, asyncio.CancelledError) as error:
            async with self._condition:
                self._ready = False
                self._condition.notify_all()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ProtocolError("Policy refresh failed") from None

    async def _poll(self):
        while True:
            delay = self._policy["refresh_after_ms"] / 1000 if self._ready else 1
            await asyncio.sleep(delay)
            with suppress(ProtocolError):
                await self.refresh()

    async def _acquire(self, name):
        async with self._condition:
            while True:
                if self._closed:
                    raise OperationError("client_closed", "not_started")
                if self._ready and (self._dispatcher or self._active < self._policy["limits"]["max_outstanding"]):
                    operation = next((op for op in self._policy["operations"] if op["name"] == name), None)
                    if operation is None:
                        raise OperationError("operation_not_permitted", "not_started")
                    if not self._dispatcher:
                        self._active += 1
                    return copy.deepcopy(operation)
                await self._condition.wait()

    async def call(self, operation, params, *, timeout=120, deadline_unix_ms=None):
        """Call a discovered operation by name with one deadline and five attempts.

        An absolute Unix deadline overrides `timeout`; it is preserved when using
        the dispatcher. A deadline must be no more than one hour in the future.
        """
        if self._policy is None and self._refresher is None:
            raise ProtocolError("Enter the client with async with before calling operations")
        if deadline_unix_ms is not None and type(deadline_unix_ms) not in (float, int):
            raise ProtocolError("Invalid task deadline")
        duration = timeout if deadline_unix_ms is None else deadline_unix_ms / 1000 - time.time()
        if type(duration) not in (float, int) or not math.isfinite(duration) or duration > 3600:
            raise ProtocolError("Invalid task deadline")
        if duration <= 0:
            raise DeadlineExceeded("task_deadline", "not_started")
        deadline_unix_ms = deadline_unix_ms if deadline_unix_ms is not None else (time.time() + duration) * 1000
        attempts, may_have_executed = 0, False
        try:
            async with asyncio.timeout(duration):
                for number in range(1, 6):
                    descriptor = await self._acquire(operation)
                    try:
                        try:
                            jsonschema.Draft202012Validator(descriptor["input_schema"]).validate(params)
                            json.dumps(params, allow_nan=False)
                        except (TypeError, ValueError, jsonschema.ValidationError, jsonschema.SchemaError):
                            raise OperationError("invalid_parameters", "not_started", attempts=attempts) from None
                        if self._dispatcher:
                            may_have_executed = True
                            reply = await self._dispatcher.call(descriptor, params, deadline_unix_ms=deadline_unix_ms)
                            if not reply["ok"]:
                                raise OperationError(reply["code"], reply["execution"], status=reply.get("status"), attempts=reply.get("attempts"))
                            return reply["body"]
                        attempts = number
                        may_have_executed = True
                        try:
                            response = await self._request("POST", descriptor["path"], params=params)
                        except (httpx.HTTPError, ProtocolError):
                            raise OperationError("transport_error", "unknown", attempts=attempts) from None
                    finally:
                        if not self._dispatcher:
                            async with self._condition:
                                self._active -= 1
                                self._condition.notify_all()
                    if response.is_success:
                        try:
                            return response.json()
                        except ValueError:
                            raise OperationError("invalid_response", "unknown", attempts=attempts) from None
                    try:
                        error = response.json().get("error", {})
                    except (ValueError, AttributeError):
                        error = {}
                    known = response.status_code in (400, 401, 403, 422, 429) or (
                        response.status_code == 503 and isinstance(error, dict)
                        and error.get("code") in ("queue_timeout", "gateway_closed") and error.get("execution") == "not_started")
                    may_have_executed = not known
                    if not safe_to_retry(response) or number == 5:
                        code = error.get("code", "http_error") if isinstance(error, dict) else "http_error"
                        if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
                            code = "http_error"
                        raise OperationError(code, "unknown" if may_have_executed else "not_started", status=response.status_code, attempts=attempts)
                    raw = response.headers.get("Retry-After", "1").strip()
                    seconds = int(raw) if raw.isascii() and raw.isdigit() else 1
                    if seconds >= deadline_unix_ms / 1000 - time.time():
                        raise DeadlineExceeded("task_deadline", "not_started", attempts=attempts)
                    await asyncio.sleep(seconds)
        except TimeoutError:
            raise DeadlineExceeded("task_deadline", "unknown" if may_have_executed else "not_started", attempts=attempts) from None

    async def aclose(self):
        """Stop new attempts and the owned refresher; close the HTTP transport."""
        async with self._condition:
            self._closed, self._ready = True, False
            self._condition.notify_all()
        if self._refresher is not None:
            self._refresher.cancel()
            with suppress(asyncio.CancelledError):
                await self._refresher
            self._refresher = None
        await self._http.aclose()
