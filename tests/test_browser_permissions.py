"""thebell's device check is a browser permission prompt (차단/허용).

That prompt is browser chrome — Selenium can neither see nor click it — so the
permission is granted before the page can ask. These cover the wiring; the
grant itself was verified against a real browser (granted vs denied).
"""
from types import SimpleNamespace

from app.services import browser as b


class _Driver:
    def __init__(self, fail_cdp=False, script=None):
        self.calls = []
        self.fail_cdp = fail_cdp
        self._script = script

    def execute_cdp_cmd(self, cmd, params):
        if self.fail_cdp:
            raise RuntimeError("CDP unavailable")
        self.calls.append((cmd, params))
        return {}

    def execute_script(self, *_a, **_k):
        if isinstance(self._script, Exception):
            raise self._script
        return self._script


def test_grants_local_network_access_for_every_thebell_origin():
    """The prompt thebell raises is Local Network Access, not notifications:
    its security program listens locally and the page has to reach it."""
    d = _Driver()
    assert b.grant_thebell_permissions(d) is True

    origins = [p["origin"] for _, p in d.calls]
    assert set(origins) == set(b.THEBELL_ORIGINS)
    for _, params in d.calls:
        assert "localNetworkAccess" in params["permissions"]


def test_grant_targets_only_thebell():
    d = _Driver()
    b.grant_thebell_permissions(d)
    for _, params in d.calls:
        assert "thebell.co.kr" in params["origin"]


def test_grant_failure_is_reported_not_raised():
    # Login must still be reachable by hand when CDP isn't available.
    assert b.grant_thebell_permissions(_Driver(fail_cdp=True)) is False


def test_permission_probe_reads_what_the_page_asked_for():
    assert b.permission_requests(_Driver(script=["notifications"])) == ["notifications"]


def test_permission_probe_tolerates_a_page_without_the_probe():
    assert b.permission_requests(_Driver(script=None)) == []
    assert b.permission_requests(_Driver(script=RuntimeError("no window"))) == []


def test_probe_script_records_notification_requests():
    # The injected script has to hook the call the site actually makes.
    assert "Notification.requestPermission" in b._PERMISSION_PROBE_JS
    assert "__permAsks" in b._PERMISSION_PROBE_JS
