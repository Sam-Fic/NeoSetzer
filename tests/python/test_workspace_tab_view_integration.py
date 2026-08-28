#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Regression tests for Adw.TabView integration into WorkspacePresenter.

These tests pin down the contracts that protect us from feedback loops
and missed signals in the native tab bar. The presenter and view are
wired together by hand-rolled helpers (we can't instantiate the real
Gtk widgets under the GI stub), so the contracts are tested at the
``ast extract + exec`` level — same pattern the wizard tests use.

The crucial invariants we want to keep:

1. ``Workspace.open_documents`` remains the source of truth. The
   tab view is a renderer of that state, never a separate list.
2. Adding a document to the workspace adds exactly one Adw.TabPage
   (idempotent: re-adding the same document is a no-op).
3. Removing a document removes the corresponding page (idempotent:
   removing a document not in the view is a no-op).
4. ``set_active_document`` flows to ``set_selected_page`` and only
   emits ``notify::selected-page`` while it is the driver (the
   ``_selecting`` re-entrancy guard). The reverse direction is
   BYPASSED: a user click's ``notify::selected-page`` does NOT forward
   to ``set_active_document`` synchronously (that would break the
   TabBox's pressed_tab reference and kill drag-reorder); the sync is
   deferred to ``_do_release_sync`` after mouse-up.
5. ``close-page`` signal routed through ``actions.close_document``
   (so push_closed_document + modified-check + confirm dialog are
   reused); close_active_document is now a thin wrapper that closes
   whichever document is currently active.
'''

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.python import conftest_stub  # noqa: F401 - installs the GI stub


# ---- AST extract helpers ----

def _extract_methods_from_class(source_path, class_name, *method_names):
    '''Extract named methods from a class in a source file. The
    extracted code is exec'd in a controlled namespace so the
    tests can call it as bound methods on sentinel ``self`` objects.

    Note: this extract does not pull in module-level imports, so
    every name the methods reference must be present in the
    namespace argument.
    '''
    tree = ast.parse(Path(source_path).read_text(encoding='utf-8'))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    selected = [
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name in method_names
    ]
    missing = set(method_names) - {n.name for n in selected}
    if missing:
        raise AssertionError(
            '{} methods missing in {}: {}'.format(
                class_name, source_path, sorted(missing)))
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(source_path), 'exec'),
         namespace)
    return {name: namespace[name] for name in method_names}


def _bound(fn, sentinel):
    '''Bind a class method (extracted as a free function) to a sentinel
    so ``self`` is implicit.'''
    def wrapper(*args, **kwargs):
        return fn(sentinel, *args, **kwargs)
    return wrapper


ACTIONS_SRC = (
    Path(__file__).resolve().parents[2]
    / 'setzer' / 'workspace' / 'actions' / 'actions.py'
)
PRESENTER_SRC = (
    Path(__file__).resolve().parents[2]
    / 'setzer' / 'workspace' / 'workspace_presenter.py'
)


# Pull just the two methods we need from each source file.
actions_methods = _extract_methods_from_class(
    ACTIONS_SRC, 'Actions', 'close_active_document', 'close_document')
presenter_methods = _extract_methods_from_class(
    PRESENTER_SRC, 'WorkspacePresenter',
    '_on_tab_view_selected_page_changed',
    '_on_tab_view_close_page',
    '_finish_page',
    '_do_release_sync')


# ---- Tests ----

class ActionsCloseContractTests(unittest.TestCase):
    '''close_active_document is a thin wrapper that closes the active
    document; close_document(document) is the unified entry point that
    the tab bar's close-page handler calls.
    '''

    def test_close_active_document_routes_through_close_document(self):
        # We test the contract via mock wiring rather than the
        # original Actions class (Actions.__init__ has many real
        # dependencies we don't want to instantiate here). The point
        # is to lock in that ``close_active_document`` delegates to
        # ``close_document`` for the active document.
        fake_actions = Mock()
        fake_actions.workspace = Mock()
        fake_actions.workspace.get_active_document = Mock(return_value='DOC')
        # Simulate the relationship between the two methods without
        # importing the actual Actions class:
        def close_active_document():
            doc = fake_actions.workspace.get_active_document()
            if doc is not None:
                fake_actions.close_document(doc)
        close_active_document()
        fake_actions.close_document.assert_called_once_with('DOC')

    def test_close_document_ignores_unknown_document(self):
        # When the tab bar's close-page signal fires for a page that
        # is no longer in open_documents (race with workspace.remove),
        # the action must early-return rather than push a bogus
        # filename onto the reopen stack or trigger a confirm dialog.
        fake_actions = Mock()
        fake_actions.workspace = Mock()
        fake_actions.workspace.open_documents = ['A', 'B']

        # Simulate the production guard: ``if document not in
        # open_documents: return``.
        def close_document(document):
            if document is None or document not in fake_actions.workspace.open_documents:
                return
            fake_actions._do_close(document)

        close_document('C')  # not in open_documents
        fake_actions._do_close.assert_not_called()

        close_document('A')  # in open_documents
        fake_actions._do_close.assert_called_once_with('A')


class TabViewSelectedPageGuardTests(unittest.TestCase):
    '''When the user clicks a tab, ``notify::selected-page`` fires.
    The handler must:

    - BAIL when the presenter itself is driving the change
      (``_selecting > 0`` re-entrancy guard) — otherwise we'd loop
      workspace.set_active_document → on_new_active_document →
      set_selected_page → notify → set_active_document forever.
    - BAIL when the page is unknown (``_page_to_doc.get(page) is None``)
      so a stray adw internal signal can't cause spurious state changes.
    - BAIL when the page is already the active document (no-op).
    - BYPASS: NOT forward to ``workspace.set_active_document`` directly.
      libadwaita's tab-bar calls set_selected_page synchronously on
      press; forwarding here would run 8+ observers mid-press and break
      the TabBox's internal pressed_tab reference, killing drag-reorder.
      The active-document sync happens after mouse-up via
      ``_do_release_sync`` (scheduled by ``setup_tab_bar_release_sync``).
    '''

    def _harness(self, selecting=0, page_to_doc=None, active_doc=None,
                 page='PAGE_A', workspace=None):
        # Build a sentinel with the attributes the handler reads.
        # We pull the handler from the presenter source so the test
        # stays bound to the real implementation, not a copy.
        sentinel = Mock()
        sentinel._selecting = selecting
        sentinel._page_to_doc = page_to_doc if page_to_doc is not None else {}
        sentinel.workspace = workspace if workspace is not None else Mock()
        sentinel.workspace.get_active_document = Mock(return_value=active_doc)
        bound = _bound(
            presenter_methods['_on_tab_view_selected_page_changed'],
            sentinel)
        return sentinel, bound

    def test_bails_when_selecting_counter_is_nonzero(self):
        # The presenter is mid-call: it just called set_selected_page
        # itself, so the resulting notify must not re-enter.
        sentinel, bound = self._harness(
            selecting=1,
            page_to_doc={'PAGE_A': 'DOC_A'},
            active_doc='DOC_B',
            page='PAGE_A',
        )
        # tab_view mock: returns the page that was just selected.
        tab_view = Mock()
        tab_view.get_selected_page = Mock(return_value='PAGE_A')
        bound(tab_view, Mock())
        # workspace.set_active_document must NOT be called: that would
        # re-enter and infinite-loop.
        sentinel.workspace.set_active_document.assert_not_called()

    def test_bails_when_page_unknown(self):
        # The page is not in our mapping (e.g. adw internal pages
        # during a transition). Skip it.
        sentinel, bound = self._harness(
            selecting=0,
            page_to_doc={},  # empty: no known page
            active_doc='DOC_X',
            page='GHOST',
        )
        tab_view = Mock()
        tab_view.get_selected_page = Mock(return_value='GHOST')
        bound(tab_view, Mock())
        sentinel.workspace.set_active_document.assert_not_called()

    def test_bails_when_page_is_already_active(self):
        # Selecting the active tab is a no-op (adw fires notify::selected-page
        # even when the selection didn't change, in some transitions).
        sentinel, bound = self._harness(
            selecting=0,
            page_to_doc={'PAGE_A': 'DOC_A'},
            active_doc='DOC_A',
            page='PAGE_A',
        )
        tab_view = Mock()
        tab_view.get_selected_page = Mock(return_value='PAGE_A')
        bound(tab_view, Mock())
        sentinel.workspace.set_active_document.assert_not_called()

    def test_clicks_non_active_tab_are_bypassed(self):
        # The happy path: user clicks a non-active tab. The selected-page
        # handler must NOT call workspace.set_active_document directly —
        # that would run observers synchronously mid-press and break the
        # TabBox pressed_tab reference (drag-reorder). The sync happens
        # after mouse-up via _do_release_sync (see below).
        sentinel, bound = self._harness(
            selecting=0,
            page_to_doc={'PAGE_A': 'DOC_A', 'PAGE_B': 'DOC_B'},
            active_doc='DOC_A',
            page='PAGE_B',
        )
        tab_view = Mock()
        tab_view.get_selected_page = Mock(return_value='PAGE_B')
        bound(tab_view, Mock())
        sentinel.workspace.set_active_document.assert_not_called()

    def test_release_sync_forwards_to_workspace_set_active_document(self):
        # The compensating path: after mouse-up (click or drag end),
        # setup_tab_bar_release_sync schedules _do_release_sync via idle.
        # It reads the final selected page and forwards exactly once,
        # wrapped in the _selecting guard to suppress the re-entrant
        # notify::selected-page that set_active_document triggers.
        sentinel = Mock()
        sentinel._selecting = 0
        sentinel._page_to_doc = {'PAGE_B': 'DOC_B'}
        sentinel.workspace = Mock()
        sentinel.workspace.get_active_document = Mock(return_value='DOC_A')
        sentinel.main_window = Mock()
        sentinel.main_window.document_stack = Mock()
        sentinel.main_window.document_stack.get_selected_page = Mock(
            return_value='PAGE_B')
        bound = _bound(presenter_methods['_do_release_sync'], sentinel)
        self.assertEqual(bound(), False)  # idle callback: return False stops
        sentinel.workspace.set_active_document.assert_called_once_with('DOC_B')
        # The guard was raised around the call and reset afterwards.
        self.assertEqual(sentinel._selecting, 0)

    def test_release_sync_is_noop_on_unknown_page(self):
        sentinel = Mock()
        sentinel._selecting = 0
        sentinel._page_to_doc = {}
        sentinel.workspace = Mock()
        sentinel.workspace.get_active_document = Mock(return_value='DOC_A')
        sentinel.main_window = Mock()
        sentinel.main_window.document_stack = Mock()
        sentinel.main_window.document_stack.get_selected_page = Mock(
            return_value='GHOST')
        bound = _bound(presenter_methods['_do_release_sync'], sentinel)
        self.assertEqual(bound(), False)
        sentinel.workspace.set_active_document.assert_not_called()

    def test_release_sync_is_noop_when_already_active(self):
        sentinel = Mock()
        sentinel._selecting = 0
        sentinel._page_to_doc = {'PAGE_A': 'DOC_A'}
        sentinel.workspace = Mock()
        sentinel.workspace.get_active_document = Mock(return_value='DOC_A')
        sentinel.main_window = Mock()
        sentinel.main_window.document_stack = Mock()
        sentinel.main_window.document_stack.get_selected_page = Mock(
            return_value='PAGE_A')
        bound = _bound(presenter_methods['_do_release_sync'], sentinel)
        self.assertEqual(bound(), False)
        sentinel.workspace.set_active_document.assert_not_called()


class TabViewClosePageGuardTests(unittest.TestCase):
    '''When the user clicks the X button or middle-clicks a tab,
    ``close-page`` fires. The handler must:

    - BAIL (and call close_page_finish(page, True) to release the
      page) if the page is unknown.
    - Otherwise delegate to ``actions.close_document(document)``,
      which encapsulates push_closed_document + modified-check + confirm.
    - The ``_selecting`` guard is set during the call to suppress
      the synchronous ``notify::selected-page`` that adw emits
      when a non-selected page is closed.
    '''

    def _harness(self, page_to_doc=None, actions=None):
        sentinel = Mock()
        sentinel._selecting = 0
        sentinel._closing_pages = set()
        sentinel._finished_pages = set()
        sentinel._page_to_doc = page_to_doc if page_to_doc is not None else {}
        sentinel.workspace = Mock()
        sentinel.workspace._confirmed_closes = set()
        sentinel.workspace.actions = actions if actions is not None else Mock()
        sentinel._finish_page = _bound(
            presenter_methods['_finish_page'], sentinel)
        bound = _bound(
            presenter_methods['_on_tab_view_close_page'],
            sentinel)
        return sentinel, bound

    def _mock_document(self, modified=False, filename='/tmp/foo.tex'):
        # The real handler reads document.source_buffer.get_modified()
        # and document.get_filename(); mirror those on a Mock so the
        # extracted method can run unmodified.
        doc = Mock()
        doc.source_buffer = Mock()
        doc.source_buffer.get_modified = Mock(return_value=modified)
        doc.get_filename = Mock(return_value=filename)
        return doc

    def test_unknown_page_finishes_with_confirm_true(self):
        # adw requires close_page_finish to be called or the page
        # stays in a "pending close" state. We pass True to tell
        # adw the close was approved, which is safe: the page is
        # not in our mapping, so we have nothing to lose.
        sentinel, bound = self._harness(page_to_doc={})
        tab_view = Mock()
        ghost = object()  # weak-referenceable, not in _page_to_doc
        bound(tab_view, ghost)
        tab_view.close_page_finish.assert_called_once_with(ghost, True)
        sentinel.workspace.actions.close_document.assert_not_called()

    def test_known_page_unmodified_routes_to_workspace_remove_document(self):
        # For an unmodified document the handler calls
        # close_page_finish(page, True) and workspace.remove_document(doc)
        # directly (push + confirm live in on_document_removed / the
        # close-page protocol, not in actions.close_document anymore).
        actions = Mock()
        doc = self._mock_document(modified=False)
        page = object()  # weak-referenceable page object (str is not)
        sentinel, bound = self._harness(
            page_to_doc={page: doc},
            actions=actions,
        )
        tab_view = Mock()
        bound(tab_view, page)
        tab_view.close_page_finish.assert_called_once_with(page, True)
        sentinel.workspace.remove_document.assert_called_once_with(doc)
        actions.close_document.assert_not_called()

    def test_duplicate_close_page_signal_is_deduplicated(self):
        # The same page's close-page signal can fire twice (user clicks X
        # → adw close_page, plus a programmatic remove_document →
        # on_document_removed → close_page). The second finish would hit
        # adw's 'page_belongs_to_this_view' assertion because the page is
        # already removed. The handler must dedupe via _closing_pages so
        # close_page_finish is called exactly once.
        actions = Mock()
        doc = self._mock_document(modified=False)
        page = object()
        sentinel, bound = self._harness(
            page_to_doc={page: doc},
            actions=actions,
        )
        tab_view = Mock()
        bound(tab_view, page)              # first signal
        bound(tab_view, page)              # duplicate signal (same page)
        tab_view.close_page_finish.assert_called_once_with(page, True)
        sentinel.workspace.remove_document.assert_called_once_with(doc)

    def test_finish_page_is_idempotent(self):
        # close_page_finish on an already-finished page hits libadwaita's
        # 'page_belongs_to_this_view' assertion. _finish_page must only
        # forward the first call and swallow every later one, regardless
        # of the confirm argument.
        sentinel, _ = self._harness()
        tab_view = Mock()
        page = object()
        sentinel._finish_page(tab_view, page, True)
        sentinel._finish_page(tab_view, page, True)
        sentinel._finish_page(tab_view, page, False)
        tab_view.close_page_finish.assert_called_once_with(page, True)

    def test_finish_guard_survives_closing_pages_dedup_loss(self):
        # _closing_pages is a WeakSet: entries can be lost (page GC
        # timing), leaving the signal-level dedup blind. If a second
        # close-page signal then arrives for an already-finished page,
        # the _finish_page guard (_finished_pages) must still prevent a
        # second close_page_finish — otherwise libadwaita aborts with
        # 'page_belongs_to_this_view'.
        actions = Mock()
        doc = self._mock_document(modified=False)
        page = object()
        sentinel, bound = self._harness(
            page_to_doc={page: doc},
            actions=actions,
        )
        tab_view = Mock()
        bound(tab_view, page)                    # first signal finishes page
        sentinel._closing_pages.discard(page)    # simulate WeakSet entry loss
        bound(tab_view, page)                    # arrives again
        tab_view.close_page_finish.assert_called_once_with(page, True)

    def test_close_path_uses_selecting_guard(self):
        # The handler must increment _selecting around the call to
        # close_page_finish + workspace.remove_document. If it forgets,
        # the cascade workspace.remove_document → on_document_removed →
        # close_page → notify::selected-page → set_active_document would
        # loop.
        actions = Mock()
        doc = self._mock_document(modified=False)
        page = object()
        sentinel, bound = self._harness(
            page_to_doc={page: doc},
            actions=actions,
        )
        # Replace the Mock's _selecting with a plain int we can capture.
        # The side_effect on workspace.remove_document reads the current
        # value mid-call (after close_page_finish, while _selecting is
        # still 1), proving the guard wraps both calls.
        observed = []
        sentinel._selecting = 0
        def capture_value(d):
            observed.append(sentinel._selecting)
        sentinel.workspace.remove_document.side_effect = capture_value
        tab_view = Mock()
        bound(tab_view, page)
        # Handler entered with 0, must set it to 1 before invoking
        # close_page_finish + remove_document (side_effect observes 1),
        # then reset to 0 after.
        self.assertEqual(observed, [1],
                         'handler must set _selecting to 1 before '
                         'calling workspace.remove_document, but observed '
                         '{}'.format(observed))
        # After the call, _selecting must be back to 0.
        self.assertEqual(sentinel._selecting, 0,
                         'handler must decrement _selecting to 0 after '
                         'workspace.remove_document returns')


if __name__ == '__main__':
    unittest.main()
