"""The package's catalogue: every message translated, and the texts switch with the language."""

import gettext
from pathlib import Path

import pytest

from terrascope.core import i18n
from terrascope_br._i18n import DOMAIN, LOCALE_DIR, _

POT = LOCALE_DIR / "terrascope_br.pot"


@pytest.fixture
def language():
    yield
    i18n.activate("en")


def _pot_messages():
    from babel.messages.pofile import read_po

    with open(POT, "rb") as source:
        catalog = read_po(source)
    return [message.id for message in catalog if message.id]


def test_every_marked_message_has_a_portuguese_translation():
    from babel.messages.pofile import read_po

    po = LOCALE_DIR / "pt_BR" / "LC_MESSAGES" / "terrascope_br.po"
    with open(po, "rb") as source:
        catalog = read_po(source)
    empty = [message.id for message in catalog if message.id and not message.string]
    assert empty == []
    assert set(_pot_messages()) == {message.id for message in catalog if message.id}


def test_texts_follow_the_active_language(language):
    i18n.activate("pt_BR")
    assert _("no data yet") == "sem dados ainda"
    assert _("no update since %s") % "x" == "sem atualização desde x"
    i18n.activate("en")
    assert _("no data yet") == "no data yet"


def test_stale_status_is_english_in_english():
    i18n.activate("en")
    from terrascope_br.base import SourceLayer  # noqa: F401  (the status text lives here)
    assert _("no update since %s") % "07/11" == "no update since 07/11"
