"""Pages name their scripts and styles by content hash, and static files are always revalidated, so a browser that
cached an older script never pairs it with a newer page (Nov 4: the consent box stayed hidden under a stale home.js)."""
import hashlib
import re

from etho.app.server import STATIC
from tests.test_app_hosting import hosted


def test_pages_reference_assets_by_content_hash(tmp_path, monkeypatch):
    client, _ = hosted(tmp_path, monkeypatch)
    for path, page in (("/", "index.html"), ("/p/jabc", "project.html")):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
        refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
        raw = re.findall(r'(?:src|href)="/static/([^"]+)"', (STATIC / page).read_text())
        assert len(refs) == len(raw) >= 3
        for ref in refs:
            name, v = re.fullmatch(r"/static/([\w.-]+)\?v=([0-9a-f]{12})", ref).groups()
            assert v == hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:12]
            a = client.get(ref)
            assert a.status_code == 200 and a.headers["cache-control"] == "no-cache" and a.headers.get("etag")


def test_static_files_are_revalidated_cheaply(tmp_path, monkeypatch):
    client, _ = hosted(tmp_path, monkeypatch)
    r = client.get("/static/home.js")
    assert r.headers["cache-control"] == "no-cache"
    again = client.get("/static/home.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_the_home_page_script_reveals_the_consent_box():
    js = (STATIC / "home.js").read_text()
    assert '$("consent-row").hidden = !cfg.limits.consent_required' in js
    assert 'id="consent-row" hidden' in (STATIC / "index.html").read_text()


def test_the_hidden_attribute_beats_author_display_rules():
    # button { display: inline-block } kept the Cancel button visible on finished jobs
    css = (STATIC / "app.css").read_text()
    assert "[hidden] { display: none !important; }" in css
