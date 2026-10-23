"""Cooperative agent access: adaptive HTTP client and optional local dispatcher."""
from .client import AsyncClient, DeadlineExceeded, OperationError, ProtocolError
from .dispatcher import DispatcherClient
from .protocol import discover, execute, service_client

__all__ = ["AsyncClient", "DeadlineExceeded", "OperationError", "ProtocolError",
           "DispatcherClient", "discover", "execute", "service_client"]
