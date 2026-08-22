#!/usr/bin/env python3
# coding: utf-8

"""Pure creation-plan helpers for the document wizard."""

from dataclasses import dataclass


LETTER_CLASSES = frozenset(('letter', 'scrlttr2'))


@dataclass(frozen=True)
class CreationPlan:
    """A small, presentation-neutral description of the pending create action."""

    mode: str
    document_class: str
    title_required: bool
    ready: bool
    language: str | None
    font_package: str | None
    page_format: str | None
    landscape: bool
    packages: tuple[str, ...]
    template_name: str | None = None


def build_creation_plan(state, source_template_name=None):
    """Return a safe plan for normal wizard or immutable source-template mode."""
    if not isinstance(state, dict):
        state = {}
    document_class = state.get('document_class')
    if not isinstance(document_class, str):
        document_class = 'article'

    source_mode = isinstance(source_template_name, str) and bool(source_template_name)
    title_required = not source_mode and document_class not in LETTER_CLASSES
    title = state.get('title')
    ready = source_mode or not title_required or (isinstance(title, str) and bool(title.strip()))

    languages = state.get('languages')
    language = next(iter(languages), None) if isinstance(languages, dict) and languages else None
    settings_key = {
        'scrartcl': 'article', 'scrreprt': 'report', 'scrbook': 'book',
        'scrlttr2': 'letter',
    }.get(document_class, document_class)
    settings = state.get(settings_key)
    if not isinstance(settings, dict):
        settings = {}
    packages = state.get('packages')
    selected_packages = ()
    if isinstance(packages, dict):
        selected_packages = tuple(sorted(
            name for name, enabled in packages.items()
            if isinstance(name, str) and enabled is True))

    return CreationPlan(
        mode='source-template' if source_mode else 'wizard-settings',
        document_class=document_class,
        title_required=title_required,
        ready=ready,
        language=language if isinstance(language, str) else None,
        font_package=(state.get('font_package')
                      if isinstance(state.get('font_package'), str) else None),
        page_format=(settings.get('page_format')
                     if isinstance(settings.get('page_format'), str) else None),
        landscape=settings.get('is_landscape') is True,
        packages=selected_packages,
        template_name=source_template_name if source_mode else None,
    )
