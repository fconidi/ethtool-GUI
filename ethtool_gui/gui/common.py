"""Helpers shared by the window and its pages."""

from __future__ import annotations

import threading
import subprocess
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..core.models import DriverInfo, LinkInfo  # noqa: E402
from ..core.protocol import encode_message, iter_messages  # noqa: E402
from ..i18n import translate as _  # noqa: E402
from .helper_client import HelperError  # noqa: E402

_PERMISSION_WORDS = ("permission denied", "not permitted")


def error_text(exc: Exception) -> str:
    """A readable, translated sentence for an error from the helper."""
    if isinstance(exc, HelperError):
        if exc.code == "access_denied":
            return _("access_denied")
        if exc.code == "helper_exited":
            return _("helper_exited")
        if exc.code == "helper_unavailable":
            return _("helper_unavailable")
        return _("error_generic", code=exc.code if exc.detail is None else f"{exc.code} ({exc.detail})")
    return _("error_generic", code=type(exc).__name__)


def needs_authorization(reading: dict | None) -> bool:
    """True when an unprivileged read failed only because it needs root."""
    for info in (reading or {}).values():
        if isinstance(info, (LinkInfo, DriverInfo)):
            for name in info.__dataclass_fields__:
                field = getattr(info, name)
                reason = getattr(field, "reason", None)
                if getattr(field, "supported", True) is False and reason \
                        and any(w in reason.lower() for w in _PERMISSION_WORDS):
                    return True
    return False


def confirm_dialog(parent: Gtk.Window | None, text: str, danger: bool = False) -> bool:
    """Ask a yes/no question; ``danger`` makes the confirm button red."""
    dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.QUESTION, text=text)
    dialog.add_button(_("confirm_no"), Gtk.ResponseType.CANCEL)
    yes = dialog.add_button(_("confirm_yes"), Gtk.ResponseType.OK)
    yes.get_style_context().add_class("destructive-action" if danger else "suggested-action")
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    answer = dialog.run() == Gtk.ResponseType.OK
    dialog.destroy()
    return answer


def thread_runner(work: Callable[[], object], done: Callable[[object, Exception | None], None]) -> None:
    """Run ``work`` on a thread and deliver ``done(result, error)`` on the GTK main loop."""

    def target() -> None:
        result, error = None, None
        try:
            result = work()
        except Exception as exc:  # reported to the user by ``done``
            error = exc
        GLib.idle_add(lambda: (done(result, error), False)[1])

    threading.Thread(target=target, daemon=True).start()


def persist_call(**request) -> dict:
    """Send one request to the separate, short-lived persistence program."""
    try:
        proc = subprocess.run(["pkexec", "/usr/lib/ethtool-gui/ethtool-gui-persist"],
                              input=encode_message(request), capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("persist_unavailable", str(exc)) from exc
    if proc.returncode in (126, 127):
        raise HelperError("access_denied")
    from io import BytesIO
    reply = next(iter_messages(BytesIO(proc.stdout)), None)
    if not reply or not reply.get("ok"):
        raise HelperError(str((reply or {}).get("error", "persist_error")),
                          (reply or {}).get("reason"))
    return reply
