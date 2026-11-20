from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__, downloads, library, utilities, weather
from .artifacts import ArtifactStore
from .config import Settings
from .errors import AppError
from .file_jobs import CHECKSUM_HANDLER, CHECKSUM_KIND
from .gutenberg import GutenbergClient
from .identity import Principal
from .job_runner import OfflineRunner
from .jobs import JobQueue
from .media import Media
from .models import ModelGateway
from .network import JsonClient
from .reader import Reader
from .storage import Ledger
from .workers import WorkerPool


@dataclass(frozen=True)
class Operation:
    run: Callable[..., dict]
    admin: bool = False
    instance: bool = False
    request_id: bool = False


class Service:
    def __init__(self, settings: Settings, client: JsonClient | None = None) -> None:
        self.settings = settings
        self.ledger = Ledger(settings.state_dir)
        self.jobs = JobQueue(self.ledger)
        self.workers: WorkerPool | None = None
        self.artifacts = ArtifactStore(self.ledger)
        self.client = client or JsonClient(contact=settings.contact)
        self.books = GutenbergClient(settings.state_dir / "catalog") if client is None else client
        self.models = ModelGateway(settings)
        self.reader = Reader()
        self.media = Media()
        self.session = secrets.token_urlsafe(24)
        self.started = time.monotonic()
        self.operations = {
            "files.list": Operation(lambda p, _a, _l, instance: self.artifacts.list(
                p.storage_key, p.chat_id, instance), instance=True),
            "files.status": Operation(lambda p, args, _l, instance: self.artifacts.get(
                p.storage_key, p.chat_id, instance, args.get("id")), instance=True),
            "files.delete": Operation(lambda p, args, _l, instance: self.artifacts.delete(
                p.storage_key, p.chat_id, instance, args.get("id")), instance=True),
            "files.read": Operation(lambda p, args, _l, instance: self.read_file(p, instance, args), instance=True),
            "files.put": Operation(lambda p, args, _l, instance, request_id: self.put_file(
                p, instance, request_id, args), request_id=True),
            "jobs.list": Operation(lambda p, _a, _l, instance: self.jobs.list(
                p.storage_key, p.chat_id, instance), instance=True),
            "jobs.status": Operation(lambda p, args, _l, instance: self.jobs.get(
                p.storage_key, p.chat_id, instance, args.get("id")), instance=True),
            "jobs.control": Operation(lambda p, args, _l, instance: self.jobs.control(
                p.storage_key, p.chat_id, instance, args.get("id"), args.get("action")), instance=True),
            "library.search": Operation(lambda _p, args, _l: library.search(self.books if args.get("source", "gutenberg") == "gutenberg" else self.client, library.BookQuery.parse(args))),
            "library.deliver": Operation(lambda p, args, _l, instance: downloads.prepare(
                self.books, self.ledger, settings.state_dir / "deliveries", p, instance, args), instance=True),
            "delivery.status": Operation(lambda p, args, _l: self.ledger.delivery_status(args.get("receipt"), p.storage_key)),
            "media.inspect": Operation(lambda _p, args, _l: self.media.inspect(args)),
            "web.read": Operation(lambda _p, args, _l: self.reader.read(args)),
            "weather.locations": Operation(lambda _p, args, locale: weather.locations(self.client, args.get("query", ""), locale)),
            "weather.forecast": Operation(lambda _p, args, _l: weather.forecast(self.client, args.get("latitude"), args.get("longitude"))),
            "utilities.calculate": Operation(lambda _p, args, _l: utilities.calculate(args.get("expression"))),
            "utilities.convert": Operation(lambda _p, args, _l: utilities.convert(args.get("value"), args.get("source", ""), args.get("target", ""))),
            "utilities.time": Operation(lambda _p, args, _l: utilities.time_in(args.get("zone", "UTC"))),
            "notes.list": Operation(lambda principal, _a, _l: {"notes": self.ledger.notes(principal.storage_key)}),
            "notes.add": Operation(lambda principal, args, _l: {"id": self.ledger.add_note(principal.storage_key, args.get("text", ""))}),
            "notes.delete": Operation(lambda principal, args, _l: {"deleted": self.ledger.delete_note(principal.storage_key, args.get("id"))}),
            "assistant.ask": Operation(lambda _p, args, locale: self.models.complete_bounded(
                args.get("text"), args.get("model", "local"), args.get("history", []),
                local_only=args.get("local_only") is True, use_tools=args.get("use_tools") is True, locale=locale)),
            "admin.health": Operation(lambda _p, _a, _l: {"version": __version__, "uptime": round(time.monotonic() - self.started), "public_access": self.settings.public_access}, admin=True),
        }

    def start_workers(self, stop: threading.Event) -> WorkerPool | None:
        """Runtime-only setup after exclusive locking/recovery; never called by a member operation."""
        if self.workers is not None:
            raise AppError("job_workers_already_started")
        if stop.is_set():
            return None
        self.jobs.kinds = {CHECKSUM_KIND: CHECKSUM_HANDLER.kind}
        try:
            runner = OfflineRunner(self.jobs, {CHECKSUM_KIND: CHECKSUM_HANDLER}, artifacts=self.artifacts)
        except AppError as error:
            self.jobs.kinds = {}
            if error.code == "sandbox_unavailable":
                return None
            raise
        pool = WorkerPool(runner, stop)
        if not pool.start():
            self.jobs.kinds = {}
            return None
        self.workers = pool
        self.operations["files.checksum"] = Operation(lambda p, args, _l, instance, request_id: self.queue_checksum(
            p, instance, request_id, args), request_id=True)
        return pool

    def queue_checksum(self, principal: Principal, instance: int, request_id: str, args: dict) -> dict:
        if self.workers is None or not self.workers.healthy():
            raise AppError("job_workers_unavailable")
        if set(args) != {"id"}:
            raise AppError("invalid_checksum_job")
        artifact = self.artifacts.get(principal.storage_key, principal.chat_id, instance, args["id"])
        if artifact["state"] != "ready":
            raise AppError("artifact_not_ready")
        return self.jobs.enqueue(principal.storage_key, principal.chat_id, instance, request_id,
                                 CHECKSUM_KIND, {"artifact": artifact["id"]})

    def import_attachment(self, principal: Principal, instance: int, message_id: int, name: str, source: Path) -> dict:
        principal.require_access(self.settings)
        self.ledger.authorize(instance, principal.storage_key, principal.chat_id)
        request_id, args = f"native-save-{message_id}", {"message": message_id, "name": name}
        cached = self.ledger.reserve(instance, request_id, principal.storage_key, "native.file.save", args,
                                     0 if principal.is_admin(self.settings) else self.settings.daily_requests,
                                     self.settings.max_pending, self.settings.global_daily_requests)
        if cached:
            if not cached["ok"]:
                raise AppError(cached["error"])
            return cached["data"]
        try:
            data = self.artifacts.import_file(principal.storage_key, principal.chat_id, instance, request_id, name, source)
            response = {"id": request_id, "ok": True, "data": data}
        except AppError as error:
            response = {"id": request_id, "ok": False, "error": error.code}
        except Exception:
            response = {"id": request_id, "ok": False, "error": "artifact_import_failed"}
        self.ledger.settle(instance, request_id, response)
        if not response["ok"]:
            raise AppError(response["error"])
        return response["data"]

    def put_file(self, principal: Principal, instance: int, request_id: str, args: dict) -> dict:
        if set(args) != {"name", "data"} or not isinstance(args["data"], str) or len(args["data"]) > 16384:
            raise AppError("invalid_artifact_data")
        try:
            body = base64.b64decode(args["data"], validate=True)
        except (ValueError, binascii.Error):
            raise AppError("invalid_artifact_data") from None
        return self.artifacts.put(principal.storage_key, principal.chat_id, instance, request_id, args["name"], body)

    def read_file(self, principal: Principal, instance: int, args: dict) -> dict:
        artifact = self.artifacts.get(principal.storage_key, principal.chat_id, instance, args.get("id"))
        if artifact["bytes"] > 12288:
            raise AppError("artifact_inline_limit")
        with self.artifacts.open(principal.storage_key, principal.chat_id, instance, artifact["id"]) as stream:
            body = stream.read(12289)
        if len(body) > 12288:
            raise AppError("artifact_inline_limit")
        return {**artifact, "data": base64.b64encode(body).decode("ascii")}

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
                                         self.settings.max_pending, self.settings.global_daily_requests)
            if cached:
                if op == "files.read" and cached.get("ok"):
                    return {"id": identifier, "ok": True, "data": self.read_file(principal, instance, args)}
                return cached
            try:
                if op in {"notes.add", "notes.delete"}:
                    return self.ledger.complete_note(instance, identifier, principal.storage_key, op, args)
                if operation.request_id:
                    result = operation.run(principal, args, locale, instance, identifier)
                else:
                    result = operation.run(principal, args, locale, instance) if operation.instance else operation.run(principal, args, locale)
                response = {"id": identifier, "ok": True, "data": result}
            except AppError as error:
                response = {"id": identifier, "ok": False, "error": error.code, "retryable": error.retryable}
            except Exception:
                response = {"id": identifier, "ok": False, "error": "operation_failed", "retryable": False}
            checkpoint = response
            if op == "files.read" and response.get("ok"):
                checkpoint = {**response, "data": {key: value for key, value in response["data"].items() if key != "data"}}
            try:
                self.ledger.settle(instance, identifier, checkpoint)
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
