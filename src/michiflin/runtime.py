from __future__ import annotations

import atexit
import fcntl
import logging
import os
import signal
from pathlib import Path

from .adapter import DeltaAdapter
from .config import Settings
from .package import build
from .service import Service


def _terminate(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main() -> None:
    os.umask(0o077)
    signal.signal(signal.SIGTERM, _terminate)
    from deltabot_cli import BotCli
    from deltachat2 import events
    from deltachat2.types import EventTypeWebxdcStatusUpdate

    cli = BotCli("michiflin", log_level="warning")
    cli.add_generic_option("--miniapp-source", type=Path, default=Path("miniapp"))
    active = {}

    @cli.on_start
    def start(bot, args):
        settings = Settings.from_environment()
        settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = (settings.state_dir / "runtime.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Another runtime already owns this state directory") from None
        atexit.register(lock.close)
        accounts = [args.account] if args.account else bot.rpc.get_all_account_ids()
        if len(accounts) != 1 or not bot.rpc.is_configured(accounts[0]):
            raise RuntimeError("Exactly one configured account is required per state directory")
        service = Service(settings)
        service.ledger.recover_interrupted()
        package = build(args.miniapp_source, settings.state_dir / "packages")
        active["adapter"] = DeltaAdapter(bot.rpc, accounts[0], service, package)
        active["account"] = accounts[0]
        active["lock"] = lock
        active["adapter"].resume_deliveries()

    @cli.on(events.NewMessage(is_info=False))
    def message(_bot, account, event):
        if account != active.get("account"):
            return
        try:
            active["adapter"].new_message(event.msg)
        except Exception:
            # Core exceptions can contain private addresses and paths; do not log their text.
            logging.warning("An incoming message could not be processed safely")

    @cli.on(events.RawEvent(func=lambda event: isinstance(event, EventTypeWebxdcStatusUpdate)))
    def update(_bot, account, event):
        if account != active.get("account"):
            return
        try:
            active["adapter"].status_update(event.msg_id)
        except Exception:
            logging.warning("A workspace update could not be processed safely")

    cli.start()


if __name__ == "__main__":
    main()
