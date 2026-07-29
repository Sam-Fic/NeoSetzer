#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import sys
import threading
import traceback
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib

# Fallback for gettext '_' when translations are not yet loaded (e.g. early startup).
try:
    _ = gettext.gettext
except NameError:
    def _(s):
        return s


_excepthook_set = False


def _format_exception(exc_type, exc_value, exc_tb):
    """Format exception traceback into a string."""
    lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    return ''.join(lines)


def _show_error_dialog(title, message):
    """Show a simple error dialog. Safe to call before full app init."""
    try:
        dialog = Adw.AlertDialog(
            heading=title,
            body=message
        )
        dialog.add_response('close', _('Close'))
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        dialog.present(Gtk.Application.get_default().get_active_window())
    except Exception:
        # If even the dialog fails, at least print to stderr.
        sys.__stderr__.write(
            f'\n{title}\n{message}\n'
        )


def _handle_exception(exc_type, exc_value, exc_tb, thread_name=None):
    """Global exception handler. Shows error dialog + logs to stderr."""
    prefix = f'[{thread_name}] ' if thread_name else ''
    formatted = _format_exception(exc_type, exc_value, exc_tb)
    # Always log to stderr so developers can still capture the traceback.
    sys.__stderr__.write(
        f'\n{prefix}Unhandled exception:\n{formatted}\n'
    )
    # Build user-facing message. Show traceback in an expandable section
    # to avoid overwhelming users while keeping details available.
    title = _('An unexpected error occurred')
    short_msg = str(exc_value) if exc_value else 'No error message'
    # Limit displayed traceback to first 2000 chars to avoid massive dialogs.
    tb_text = formatted.strip()
    if len(tb_text) > 2000:
        tb_text = tb_text[:2000] + '\n… (truncated)'
    body = f'{prefix}{short_msg}\n\nTraceback:\n{tb_text}'
    # Ensure dialog is shown on the main/UI thread.
    try:
        if GLib.main_depth() > 0:
            GLib.idle_add_once(lambda: _show_error_dialog(title, body))
        else:
            _show_error_dialog(title, body)
    except Exception:
        _show_error_dialog(title, body)


def python_excepthook(exc_type, exc_value, exc_tb):
    """sys.excepthook: catch exceptions in the main thread."""
    _handle_exception(exc_type, exc_value, exc_tb)


def thread_exception_hook(args):
    """threading.excepthook: catch exceptions in non-main threads."""
    _handle_exception(
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
        thread_name=args.thread.name or 'Thread'
    )


def install():
    """Install global exception handlers. Safe to call multiple times."""
    global _excepthook_set
    if _excepthook_set:
        return
    _excepthook_set = True

    # Main thread unhandled exceptions.
    sys.excepthook = python_excepthook

    # Non-main thread unhandled exceptions (Python 3.8+).
    if hasattr(threading, 'excepthook'):
        threading.excepthook = thread_exception_hook

    # GLib main loop callback exceptions (e.g. GLib.timeout_add,
    # GLib.idle_add callbacks). Use a custom hook to prevent silent
    # failures. Note: GLib.set_exception_handler is not a standard
    # API; we use the GObject closure marshal to wrap callbacks instead.
    # For that, see gtk_widget_set_exception_handler below.
    try:
        from gi.repository import GObject
        _wrap_all_closure_marshal(GObject)
    except Exception:
        pass


_original_marshal = None


def _wrap_all_closure_marshal(GObject_module):
    """Wrap GObject.Closure.marshal to catch exceptions in GLib callbacks.

    This is applied once; subsequent closures will be created with the
    wrapped marshal, so timeout_add, idle_add, signal handlers, etc.
    all funnel into our excepthook instead of silently dying.
    """
    global _original_marshal
    if _original_marshal is not None:
        return
    _original_marshal = GObject_module.Closure.marshal

    def safe_marshal(closure, return_value, n_param_values, param_values,
                     invocation_hint, marshal_data):
        try:
            _original_marshal(
                closure, return_value, n_param_values,
                param_values, invocation_hint, marshal_data
            )
        except SystemExit:
            raise
        except Exception as e:
            # Build a fake traceback when we don't have a real one.
            tb = e.__traceback__ if hasattr(e, '__traceback__') else None
            # Determine thread name via the current thread object.
            thread = threading.current_thread()
            tname = thread.name or 'Thread'
            _handle_exception(type(e), e, tb, thread_name=f'GLib callback [{tname}]')
            # swallow the exception so the main loop keeps running.

    # Replace the closure marshal.
    GObject_module.Closure.marshal = staticmethod(safe_marshal)


def uninstall():
    """Restore original exception handlers."""
    global _excepthook_set
    if not _excepthook_set:
        return
    _excepthook_set = False
    sys.excepthook = sys.__excepthook__
    if hasattr(threading, 'excepthook'):
        threading.excepthook = threading.__excepthook__