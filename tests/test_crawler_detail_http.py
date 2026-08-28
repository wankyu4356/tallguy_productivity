"""Detail extraction over HTTP, and the login flow's window handling.

The crawler used to open every article in the browser just to read its date
and summary; both are in the page HTML, so these cover the parsing and the
fall-through that keeps a blocked page from costing accuracy.
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

import pytest

from app.models.schemas import ArticleInfo
from app.services import crawler as cr


ARTICLE_HTML = """
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="description" content="리벨리온이 기업공개(IPO)를 앞두고 이사회를 9인 체제로 확대한다. 독립이사 3명을 새로 선임한다.">
<meta property="og:description" content="리벨리온이 기업공개(IPO)를 앞두고 이사회를 9인 체제로 확대한다.">
</head><body>
<div class="info"><span class="date" tooltip="입력 2026-08-12 오후 5:35:56">2026-08-12 17:35:55</span></div>
</body></html>
"""


def _article(**kw):
    base = dict(id="a1", title="t", url="https://www.thebell.co.kr/front/newsview.asp?key=1",
                category="Deal", subcategory="Deal")
    base.update(kw)
    return ArticleInfo(**base)


def test_extracts_date_and_summary():
    date_text, summary = cr._extract_detail(ARTICLE_HTML)
    assert date_text == "2026-08-12 17:35:55"
    assert summary.startswith("리벨리온이 기업공개")
    assert cr._parse_datetime(date_text).year == 2026


def test_summary_entities_are_unescaped():
    page = '<meta name="description" content="법무&middot;기술 분야 독립이사 3명을 새로 선임하면서 체계를 갖춘다.">'
    _, summary = cr._extract_detail(page)
    assert "&middot;" not in summary
    assert "·" in summary


def test_too_short_summary_is_rejected():
    _, summary = cr._extract_detail('<meta name="description" content="짧음">')
    assert summary is None


def test_decode_handles_euckr_and_utf8():
    assert "한글" in cr._decode_page("한글".encode("utf-8"))
    assert "한글" in cr._decode_page("한글".encode("euc-kr"))


def test_http_path_fills_articles_and_reports_leftovers(monkeypatch):
    arts = [_article(id="a1", url="https://x/1"),
            _article(id="a2", url="https://x/2"),
            _article(id="a3", url="https://x/3")]
    # a3's page comes back empty, the way a blocked or changed page would
    pages = {"https://x/1": ARTICLE_HTML, "https://x/2": ARTICLE_HTML, "https://x/3": None}
    monkeypatch.setattr(cr, "_http_get", lambda u, c: pages[u])
    driver = SimpleNamespace(get_cookies=lambda: [{"name": "s", "value": "1"}])

    leftover = cr._fetch_details_over_http(driver, arts)

    assert [a.id for a in leftover] == ["a3"], "unresolved articles must fall through"
    # _parse_datetime normalises to KST at minute precision
    assert arts[0].published_at == datetime(2026, 8, 12, 17, 35, tzinfo=KST)
    assert arts[1].summary.startswith("리벨리온")
    assert arts[2].published_at is None


def test_http_path_never_overwrites_existing_values(monkeypatch):
    known = datetime(2020, 1, 1, 9, 0, tzinfo=KST)
    a = _article(published_at=known, summary="이미 있는 요약입니다. 덮어쓰면 안 됩니다.")
    monkeypatch.setattr(cr, "_http_get", lambda u, c: ARTICLE_HTML)
    cr._fetch_details_over_http(SimpleNamespace(get_cookies=lambda: []), [a])

    assert a.published_at == known
    assert a.summary.startswith("이미 있는")


def test_cookie_header_survives_a_broken_driver():
    assert cr._cookie_header(SimpleNamespace(get_cookies=lambda: [])) == ""

    def boom():
        raise RuntimeError("session gone")
    assert cr._cookie_header(SimpleNamespace(get_cookies=boom)) == ""


# --- login: device prompt + window recovery --------------------------------

class _Driver:
    """Minimal Selenium stand-in: a set of windows, one of them focused."""
    def __init__(self, windows: dict[str, str], current: str):
        self.windows = windows           # handle -> body text
        self.current = current
        self.switch_to = SimpleNamespace(window=self._switch)

    def _switch(self, handle):
        if handle not in self.windows:
            raise RuntimeError("no such window")
        self.current = handle

    @property
    def window_handles(self):
        return list(self.windows)

    @property
    def current_url(self):
        if self.current not in self.windows:
            raise RuntimeError("no such window")
        return "https://www.thebell.co.kr/"

    def execute_script(self, *_a, **_k):
        if self.current not in self.windows:
            raise RuntimeError("no such window")
        return self.windows[self.current]


def test_device_prompt_is_recognised():
    d = _Driver({"w": "이 기기를 등록하시겠습니까? [허용] [취소]"}, "w")
    assert cr._looks_like_device_prompt(d)


def test_ordinary_page_is_not_a_device_prompt():
    d = _Driver({"w": "오늘의 주요 뉴스와 시장 동향을 확인하세요"}, "w")
    assert not cr._looks_like_device_prompt(d)


def test_device_prompt_found_in_popup_and_focused():
    d = _Driver({"main": "로그인 페이지입니다",
                 "popup": "이 기기에서 접속을 허용하시겠습니까?"}, "main")
    found = cr._device_prompt_window(d, "main")
    assert found == "popup"
    assert d.current == "popup", "the user's click has to land on the live window"


def test_focus_returns_to_main_when_no_prompt():
    d = _Driver({"main": "로그인", "popup": "광고 배너"}, "main")
    assert cr._device_prompt_window(d, "main") is None
    assert d.current == "main"


def test_closed_window_is_recovered():
    d = _Driver({"main": "로그인 페이지"}, "popup")   # focused on a window that's gone
    cr._ensure_live_window(d)
    assert d.current == "main"


def test_login_check_survives_a_dead_window():
    d = _Driver({}, "gone")
    assert cr._check_logged_in(d, quiet=True) is False
