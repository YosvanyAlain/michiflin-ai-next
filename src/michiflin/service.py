from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import __version__, agent, downloads, library, utilities, weather
from .config import Settings
from .errors import AppError
from .identity import Principal
from .models import ModelGateway
from .network import JsonClient
from .storage import Ledger


@dataclass(frozen=True)
class Operation:
    run: Callable[[Principal, dict, str], dict]
    admin: bool = False
    instance: bool = False


class Service:
    def __init__(self, settings: Settings, client: JsonClient | None = None) -> None:
        self.settings = settings
        self.ledger = Ledger(settings.state_dir)
        self.client = client or JsonClient(contact=settings.contact)
        self.models = ModelGateway(settings)
        self.session = secrets.token_urlsafe(24)
        self.started = time.monotonic()
        self.operations = {
            "library.search": Operation(lambda _p, args, _l: library.search(self.client, library.BookQuery.parse(args))),
            "library.deliver": Operation(lambda p, args, _l, instance: downloads.prepare(
                self.client, self.ledger, settings.state_dir / "deliveries", p, instance, args), instance=True),
            "delivery.status": Operation(lambda p, args, _l: self.ledger.delivery_status(args.get("receipt"), p.storage_key)),
            "weather.locations": Operation(lambda _p, args, locale: weather.locations(self.client, args.get("query", ""), locale)),
            "weather.forecast": Operation(lambda _p, args, _l: weather.forecast(self.client, args.get("latitude"), args.get("longitude"))),
            "utilities.calculate": Operation(lambda _p, args, _l: utilities.calculate(args.get("expression"))),
            "utilities.convert": Operation(lambda _p, args, _l: utilities.convert(args.get("value"), args.get("source", ""), args.get("target", ""))),
            "utilities.time": Operation(lambda _p, args, _l: utilities.time_in(args.get("zone", "UTC"))),
            "notes.list": Operation(lambda principal, _a, _l: {"notes": self.ledger.notes(principal.storage_key)}),
            "notes.add": Operation(lambda principal, args, _l: {"id": self.ledger.add_note(principal.storage_key, args.get("text", ""))}),
            "notes.delete": Operation(lambda principal, args, _l: {"deleted": self.ledger.delete_note(principal.storage_key, args.get("id"))}),
            "assistant.ask": Operation(lambda _p, args, locale: self.models.complete(
                args.get("text"), args.get("model", "local"), args.get("history", []), local_only=args.get("local_only") is True,
                toolset=agent.tools(self.client, local_only=args.get("local_only") is True or args.get("model") == "local", locale=locale)
                if args.get("use_tools") is True else None)),
            "admin.health": Operation(lambda _p, _a, _l: {"version": __version__, "uptime": round(time.monotonic() - self.started), "public_access": self.settings.public_access}, admin=True),
        }

    def register_instance(self, principal: Principal, instance: int) -> None:
        principal.require_access(self.settings)
        self.ledger.bind(instance, principal.storage_key, principal.chat_id)

    def handle(self, principal: Principal, instance: int, request: object) -> dict:
        identifier = ""
        try:
            principal.require_access(self.settings)
            self.ledger.authorize(instance, principal.storage_key, principal.chat_id)
            if not isinstance(request, dict):
                raise AppError("invalid_request")
            identifier = request.get("id", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{12,80}", identifier):
                raise AppError("invalid_request_id")
            try:
                encoded = json.dumps(request, allow_nan=False).encode()
            except (TypeError, ValueError):
                raise AppError("invalid_request") from None
            if len(encoded) > 32000:
                raise AppError("request_too_large")
            op, args = request.get("op"), request.get("args", {})
            if not isinstance(op, str) or not isinstance(args, dict):
                raise AppError("invalid_request")
            locale = "es" if request.get("locale") == "es" else "en"
            if op == "hello":
                return {"id": identifier, "ok": True, "data": {"version": __version__, "session": self.session,
                        "role": "admin" if principal.is_admin(self.settings) else "member", "public_access": self.settings.public_access,
                        "models": self.models.catalogue(), "daily_requests": self.settings.daily_requests,
                        "operations": [name for name, operation in self.operations.items()
                                       if not operation.admin or principal.is_admin(self.settings)]}}
            operation = self.operations.get(op)
            if operation is None:
                raise AppError("operation_unavailable")
            if operation.admin:
                principal.require_admin(self.settings)
            session = request.get("session", "")
            if not isinstance(session, str) or not secrets.compare_digest(session.encode(), self.session.encode()):
                raise AppError("session_changed")
            cached = self.ledger.reserve(instance, identifier, principal.storage_key, op, args,
                                         0 if principal.is_admin(self.settings) else self.settings.daily_requests,
                                         self.settings.max_pending)
            if cached:
                return cached
            try:
                result = operation.run(principal, args, locale, instance) if operation.instance else operation.run(principal, args, locale)
                response = {"id": identifier, "ok": True, "data": result}
            except AppError as error:
                response = {"id": identifier, "ok": False, "error": error.code, "retryable": error.retryable}
            except Exception:
                response = {"id": identifier, "ok": False, "error": "operation_failed", "retryable": False}
            try:
                self.ledger.settle(instance, identifier, response)
            except (TypeError, ValueError):
                response = {"id": identifier, "ok": False, "error": "result_invalid", "retryable": False}
                self.ledger.settle(instance, identifier, response)
            except AppError as error:
                if error.code != "result_too_large":
                    raise
                response = {"id": identifier, "ok": False, "error": error.code, "retryable": False}
                self.ledger.settle(instance, identifier, response)
            return response
        except AppError as error:
            return {"id": identifier if isinstance(identifier, str) else "", "ok": False,
                    "error": error.code, "retryable": error.retryable}
