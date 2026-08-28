"""recommend_articles must fan its batches out, not walk them one by one.

These stub the Anthropic client so they run without an API key. The point is
the orchestration: concurrency, ordering, progress, and that a failing batch
degrades to "pick it yourself" instead of dropping articles.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models.schemas import ArticleInfo
from app.services import llm_classifier as lc


def _articles(n: int) -> list[ArticleInfo]:
    return [ArticleInfo(id=f"a{i}", title=f"제목 {i}", url=f"https://x/{i}",
                        category="Deal", subcategory="Deal") for i in range(n)]


class _StubMessages:
    def __init__(self, recorder, delay=0.05, fail_on=None):
        self.recorder = recorder
        self.delay = delay
        self.fail_on = fail_on or set()

    async def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        ids = [line.split("]")[0][1:] for line in prompt.splitlines()
               if line.startswith("[")]
        self.recorder["inflight"] += 1
        self.recorder["peak"] = max(self.recorder["peak"], self.recorder["inflight"])
        self.recorder["kwargs"].append(kwargs)
        try:
            await asyncio.sleep(self.delay)
            if ids and ids[0] in self.fail_on:
                raise RuntimeError("boom")
            payload = {"recommendations": [
                {"article_id": i, "recommended": True, "reason": "ok"} for i in ids]}
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))])
        finally:
            self.recorder["inflight"] -= 1


def _install_stub(monkeypatch, **kw):
    recorder = {"inflight": 0, "peak": 0, "kwargs": []}
    client = SimpleNamespace(messages=_StubMessages(recorder, **kw))
    monkeypatch.setattr(lc, "_get_async_client", lambda: client)
    return recorder


@pytest.mark.asyncio
async def test_batches_run_concurrently(monkeypatch):
    recorder = _install_stub(monkeypatch)
    arts = _articles(lc.RECOMMEND_BATCH_SIZE * 4)

    recs = await lc.recommend_articles(arts)

    assert recorder["peak"] > 1, "batches were still serialised"
    assert len(recs) == len(arts)


@pytest.mark.asyncio
async def test_concurrency_is_capped(monkeypatch):
    recorder = _install_stub(monkeypatch)
    arts = _articles(lc.RECOMMEND_BATCH_SIZE * (lc.RECOMMEND_CONCURRENCY + 3))

    await lc.recommend_articles(arts)

    assert recorder["peak"] <= lc.RECOMMEND_CONCURRENCY


@pytest.mark.asyncio
async def test_results_keep_article_order(monkeypatch):
    _install_stub(monkeypatch)
    arts = _articles(lc.RECOMMEND_BATCH_SIZE * 3)

    recs = await lc.recommend_articles(arts)

    assert [r.article_id for r in recs] == [a.id for a in arts]


@pytest.mark.asyncio
async def test_failed_batch_falls_back_instead_of_dropping(monkeypatch):
    _install_stub(monkeypatch, fail_on={"a0"})
    arts = _articles(lc.RECOMMEND_BATCH_SIZE * 2)

    recs = await lc.recommend_articles(arts)

    assert len(recs) == len(arts)
    assert all(r.recommended for r in recs[:lc.RECOMMEND_BATCH_SIZE])
    assert "수동 선택" in recs[0].reason


@pytest.mark.asyncio
async def test_progress_reports_every_batch(monkeypatch):
    _install_stub(monkeypatch)
    arts = _articles(lc.RECOMMEND_BATCH_SIZE * 3)
    seen = []

    await lc.recommend_articles(arts, on_step=lambda c, t, d="": seen.append((c, t)))

    totals = {t for _, t in seen}
    assert totals == {3}
    assert [c for c, _ in seen] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_low_effort_is_requested(monkeypatch):
    recorder = _install_stub(monkeypatch)
    monkeypatch.setattr(lc, "_EFFORT_SUPPORTED", True)

    await lc.recommend_articles(_articles(3))

    assert recorder["kwargs"][0]["output_config"] == {"effort": "low"}
