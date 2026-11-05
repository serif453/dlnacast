"""asyncio döngüsünü ayrı thread'de çalıştırıp sonuçları GTK ana döngüsüne taşır."""
import asyncio
import threading
from typing import Awaitable, Callable, Optional

from gi.repository import GLib


def to_ui(fn: Callable, *args) -> None:
    """Herhangi bir thread'den GTK ana thread'inde fn(*args) çalıştırır."""
    GLib.idle_add(lambda: (fn(*args), False)[1])


class AsyncBridge:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(
        self,
        coro: Awaitable,
        on_done: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ) -> None:
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def _finished(f) -> None:
            try:
                result = f.result()
            except Exception as exc:  # noqa: BLE001
                if on_error:
                    to_ui(on_error, exc)
            else:
                if on_done:
                    to_ui(on_done, result)

        fut.add_done_callback(_finished)
