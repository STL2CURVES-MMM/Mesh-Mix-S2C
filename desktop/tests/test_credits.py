"""The Credits page and the Settings button."""
import os
import re

from mmm_app import credits

HERE = os.path.dirname(os.path.abspath(__file__))
MAC_CREDITS = os.path.join(HERE, "..", "..", "app", "Sources", "Credits.swift")


def test_unnamed_supporters_are_anonymous(monkeypatch):
    monkeypatch.setattr(credits, "SUPPORTERS", [None, "  ", "Ana"])
    assert credits.supporter_names() == [credits.ANONYMOUS, credits.ANONYMOUS, "Ana"]


def test_three_anonymous_supporters_for_now():
    assert credits.supporter_names() == [credits.ANONYMOUS] * 3


def test_mac_app_lists_the_same_supporters():
    """The Mac app keeps its own copy of the list (Credits.swift); the two must match."""
    swift = open(MAC_CREDITS, encoding="utf-8").read()
    body = re.search(r"static let supporters: \[String\?\] = \[(.*?)\]", swift).group(1)
    mac = [None if t.strip() == "nil" else t.strip().strip('"') for t in body.split(",") if t.strip()]
    assert mac == credits.SUPPORTERS
    assert credits.ANONYMOUS in swift and credits.NAME_NOTE.split(" When")[0] in swift


def test_credits_dialog_lists_everyone(qapp):
    from mmm_app.widgets import Label
    d = credits.CreditsDialog()
    texts = [w.text() for w in d.findChildren(Label)]
    assert texts.count(credits.ANONYMOUS) == 3
    assert all(name in texts for name, _ in credits.MAKERS)
    assert credits.NAME_NOTE in texts
    d.deleteLater()


def test_update_window_has_credits_button(qapp):
    from mmm_app.update_ui import UpdateController, UpdateDialog
    d = UpdateDialog(UpdateController())
    assert d.credits_btn.text() == "Credits" and not d.credits_btn.isHidden()
    d.deleteLater()


def test_header_has_settings_button(qapp):
    from mmm_app.sidebar import BrandHeader
    h = BrandHeader()
    fired = []
    h.settings_clicked.connect(lambda: fired.append(1))
    h.settings_button.click()
    assert fired == [1]
    h.deleteLater()
