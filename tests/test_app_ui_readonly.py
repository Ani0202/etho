"""Static UI checks for the read-only mode: privacy notice placement, no 'unlock' wording, revise button reason."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "etho" / "app" / "static"


def test_privacy_notice_is_outside_the_form_and_keeps_its_text():
    html = (STATIC / "index.html").read_text()
    start = html.index('<form id="form"')
    form = html[start:html.index("</form>", start)]
    assert 'id="privacy"' not in form and "Privacy notice." not in form
    note = html[html.index('id="privacy"'):]
    assert "Privacy notice." in note and "Only upload video you have the right to share." in note
    assert 'id="ret-h"' in note
    assert html.index("</form>", start) < html.index('id="privacy"')


def test_sample_card_does_not_invite_visitors_to_unlock():
    html = (STATIC / "index.html").read_text()
    card = html[html.index('id="sample-card"'):html.index("</section>", html.index('id="sample-card"'))]
    assert not re.search(r"unlock", card, re.I)


def test_revise_button_states_why_it_is_off_and_has_a_disabled_style():
    js, css = (STATIC / "project.js").read_text(), (STATIC / "app.css").read_text()
    assert 'id: "revwhy"' in js and "disabled: true" in js
    # the reason names the actual cause (server.py: can_submit = live and granted and not capped)
    for reason in ("Live use is off", "Needs the access code", "Live budget used up for now"):
        assert f'"{reason}"' in js
    assert js.count("whyOff()") >= 4
    assert "button:disabled" in css and "button.primary:disabled" in css


def test_timing_card_is_width_safe():
    js, css = (STATIC / "project.js").read_text(), (STATIC / "app.css").read_text()
    assert 'class: "stagetbl"' in js and "table-layout: fixed" in css and "minmax(0, 1fr)" in css
