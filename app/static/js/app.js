// Utility functions for TheBell News Clipper

function showStatus(elementId, message, type = 'info') {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.style.display = 'flex';
    const textEl = el.querySelector('p') || el;
    textEl.textContent = message;
    if (type === 'error') textEl.classList.add('log-error');
}

function appendLog(containerId, message, type = 'info') {
    const container = document.getElementById(containerId);
    if (!container) return;
    const p = document.createElement('p');
    p.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    p.classList.add(`log-${type}`);
    container.appendChild(p);
    container.scrollTop = container.scrollHeight;
}

function selectAll(checked) {
    document.querySelectorAll('.article-checkbox').forEach(cb => {
        cb.checked = checked;
    });
    updateSelectionCount();
}

function updateSelectionCount() {
    // Articles appear in both tabs (recommend + manual), so deduplicate by value.
    const all = document.querySelectorAll('.article-checkbox');
    const uniqueIds = new Set();
    const checkedIds = new Set();
    all.forEach(cb => {
        uniqueIds.add(cb.value);
        if (cb.checked) checkedIds.add(cb.value);
    });
    const countEl = document.getElementById('selection-count');
    if (countEl) {
        countEl.textContent = `${checkedIds.size} / ${uniqueIds.size} 선택됨`;
    }
    const railEl = document.getElementById('rail-sel');
    if (railEl) railEl.textContent = checkedIds.size;
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).style.display = 'block';
}


/* ==========================================================================
   WaitUX — shared waiting experience
   Long waits (crawling ~3분, 본문 수집 ~5분+) are the worst part of this tool.
   This turns them into something legible: a real progress bar driven by the
   server's own counts, a remaining-time estimate, tab-title progress so the
   user can switch away, and a notification when it finishes.
   ========================================================================== */

const STATUS_LABELS = {
    idle: '대기 중', crawling: '기사 수집 중', crawl_done: '수집 완료',
    recommending: 'AI 추천 중', recommend_done: '추천 완료', selected: '기사 선택됨',
    generating: 'PDF 생성 중', review_ready: '목차 검수 대기', finalizing: '마무리 중',
    done: '완료', error: '오류',
};
function statusLabel(v) { return STATUS_LABELS[v] || v; }

const WaitUX = (() => {
    let host = null;
    let active = false;
    let phase = null;
    let phaseStart = 0;
    let baseTitle = document.title;
    let tipTimer = null;
    let tickTimer = null;
    let lastState = null;

    const TIPS = {
        browser: ['브라우저를 띄우는 중이에요.'],
        login:   ['더벨에 로그인하는 중이에요.',
                  '로그인 창이 뜨면 그대로 두세요. 자동으로 진행됩니다.'],
        crawl:   ['섹션별로 기사 목록을 훑는 중이에요.',
                  '보통 2~3분 정도 걸려요.',
                  '다른 창에서 일하셔도 됩니다. 끝나면 알려 드릴게요.'],
        detail:  ['기사 날짜와 요약을 채우는 중이에요.'],
        recommend: ['AI가 PE 투자 관점에서 기사를 고르는 중이에요.',
                    '기사가 많으면 묶음으로 나눠서 분석해요.',
                    '조금만 기다려 주세요. 거의 다 왔어요.'],
        fetch:   ['기사 본문을 열어 PDF로 저장하는 중이에요.',
                  '기사 수가 많으면 시간이 걸려요. 창을 닫지 마세요.',
                  '다른 창에서 일하셔도 됩니다. 끝나면 알려 드릴게요.'],
        classify:['AI가 기사를 카테고리별로 정리하는 중이에요.',
                  '목차 구조를 짜고 있어요.'],
        finalize:['PDF를 하나로 합치고 목차를 만드는 중이에요.',
                  '거의 다 됐어요.'],
    };

    function fmtDuration(ms) {
        if (!isFinite(ms) || ms < 0) return '';
        const s = Math.round(ms / 1000);
        if (s < 60) return `${s}초`;
        const m = Math.floor(s / 60);
        const r = s % 60;
        return r ? `${m}분 ${r}초` : `${m}분`;
    }

    function mount(container) {
        if (!container) return null;
        container.innerHTML = `
            <div class="wait" id="wait-panel">
                <div class="wait-head">
                    <span class="wait-label" id="wait-label">준비 중</span>
                    <span class="wait-pct" id="wait-pct"></span>
                </div>
                <div class="wait-bar"><div class="wait-bar-fill" id="wait-fill"></div></div>
                <div class="wait-meta">
                    <span class="wait-detail" id="wait-detail"></span>
                    <span class="wait-time" id="wait-time"></span>
                </div>
                <div class="wait-tip" id="wait-tip"></div>
            </div>`;
        host = container;
        return container;
    }

    function el(id) { return document.getElementById(id); }

    function rotateTips() {
        const tips = TIPS[phase] || [];
        if (!tips.length) { const t = el('wait-tip'); if (t) t.textContent = ''; return; }
        let i = 0;
        const show = () => {
            const t = el('wait-tip');
            if (!t) return;
            t.style.opacity = '0';
            setTimeout(() => { t.textContent = tips[i % tips.length]; t.style.opacity = '1'; }, 180);
            i++;
        };
        show();
        clearInterval(tipTimer);
        if (tips.length > 1) tipTimer = setInterval(show, 7000);
    }

    function tick() {
        if (!active || !lastState) return;
        const { current, total } = lastState;
        const elapsed = Date.now() - phaseStart;
        const timeEl = el('wait-time');
        if (!timeEl) return;

        let text = `${fmtDuration(elapsed)} 경과`;
        if (total > 0 && current > 0 && current < total) {
            const remaining = (elapsed / current) * (total - current);
            // Only promise a number once the estimate has settled a little, and
            // don't count down the last few seconds — "약 0초 남음" reads broken.
            if (elapsed > 4000) {
                text += remaining < 5000
                    ? ' · 거의 다 됐어요'
                    : ` · 약 ${fmtDuration(remaining)} 남음`;
            }
        }
        timeEl.textContent = text;
    }

    function start(label) {
        active = true;
        baseTitle = baseTitle || document.title;
        const panel = el('wait-panel');
        if (panel) panel.classList.add('is-on');
        if (label) { const l = el('wait-label'); if (l) l.textContent = label; }
        phaseStart = Date.now();
        clearInterval(tickTimer);
        tickTimer = setInterval(tick, 1000);
        window.addEventListener('beforeunload', guard);
    }

    function guard(e) {
        if (!active) return;
        e.preventDefault();
        e.returnValue = '';
        return '';
    }

    function onState(state) {
        if (!state) return;
        if (state.phase && state.phase !== phase) {
            phase = state.phase;
            phaseStart = Date.now();
            rotateTips();
        }
        lastState = state;

        const label = el('wait-label');
        const pct = el('wait-pct');
        const fill = el('wait-fill');
        const detail = el('wait-detail');

        if (label && state.label) label.textContent = state.label;
        if (detail) detail.textContent = state.detail || '';

        const hasTotal = state.total > 0;
        if (fill) {
            fill.classList.toggle('is-indeterminate', !hasTotal);
            fill.style.width = hasTotal ? `${state.percent}%` : '';
        }
        if (pct) {
            pct.textContent = hasTotal ? `${state.current} / ${state.total}` : '';
        }

        document.title = hasTotal
            ? `(${state.percent}%) ${state.label || ''} · ${baseTitle}`
            : `${state.label || ''} · ${baseTitle}`;
        tick();
    }

    function finish(ok, message) {
        active = false;
        clearInterval(tipTimer);
        clearInterval(tickTimer);
        window.removeEventListener('beforeunload', guard);
        document.title = baseTitle;

        const fill = el('wait-fill');
        if (fill) { fill.classList.remove('is-indeterminate'); fill.style.width = '100%'; }
        const label = el('wait-label');
        if (label && message) label.textContent = message;
        const tip = el('wait-tip');
        if (tip) tip.textContent = '';
        const panel = el('wait-panel');
        if (panel) panel.classList.toggle('is-error', !ok);

        if (document.hidden) notify(ok ? '작업이 끝났어요' : '문제가 생겼어요', message || '');
    }

    function askNotifyPermission() {
        try {
            if ('Notification' in window && Notification.permission === 'default') {
                Notification.requestPermission();
            }
        } catch (_) { /* not available — the tab title still shows progress */ }
    }

    function notify(title, body) {
        try {
            if ('Notification' in window && Notification.permission === 'granted') {
                new Notification(title, { body, icon: '/static/img/thebell-logo.png' });
            }
        } catch (_) { /* ignore */ }
    }

    return { mount, start, onState, finish, askNotifyPermission, notify,
             get active() { return active; } };
})();


/* Scroll reveal — sections fade up as they enter the viewport.
   Elements are only hidden once the observer is confirmed available, so a
   browser without IntersectionObserver simply shows everything immediately. */
(function () {
    if (!('IntersectionObserver' in window)) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    document.addEventListener('DOMContentLoaded', () => {
        const targets = document.querySelectorAll('.card, .ri-category, .category-group');
        if (!targets.length) return;

        const io = new IntersectionObserver((entries) => {
            entries.forEach((e) => {
                if (!e.isIntersecting) return;
                e.target.classList.add('is-in');
                io.unobserve(e.target);
            });
        }, { rootMargin: '0px 0px -8% 0px', threshold: 0.02 });

        targets.forEach((el, i) => {
            // Anything already on screen skips the animation entirely.
            if (el.getBoundingClientRect().top < window.innerHeight * 0.9) return;
            el.classList.add('reveal');
            el.style.transitionDelay = `${Math.min(i, 4) * 60}ms`;
            io.observe(el);
        });
    });
})();


/* ==========================================================================
   Splash controller (ref: trionn.com preloader)
   Belt wipe + slot counter + staggered tagline. Runs once per browser session
   and can be dismissed by click/key — a daily tool shouldn't gate you behind
   an intro every time.
   ========================================================================== */
(function () {
    const pl = document.getElementById('pl');
    if (!pl) return;

    const SKIP = (() => {
        try { return sessionStorage.getItem('pl-seen') === '1'; } catch (_) { return false; }
    })();
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (SKIP || reduced) {
        pl.classList.add('is-done', 'is-gone');
        return;
    }

    // Build each reel's 0-9 strip so the digits can roll like an odometer.
    const strips = Array.from(pl.querySelectorAll('.pl-strip'));
    strips.forEach(strip => {
        strip.innerHTML = Array.from({ length: 11 }, (_, n) => `<i>${n % 10}</i>`).join('');
    });

    const DIGIT_H = 16;
    const rail = document.getElementById('pl-rail-fill');
    function showCount(n) {
        const v = Math.min(100, Math.max(0, n));
        const s = String(v).padStart(3, '0');
        strips.forEach((strip, i) => {
            strip.style.transform = `translateY(-${Number(s[i]) * DIGIT_H}px)`;
        });
        if (rail) rail.style.transform = `scaleX(${v / 100})`;
    }

    const words = Array.from(pl.querySelectorAll('.pl-word'));
    const dots = Array.from(pl.querySelectorAll('.pl-dot'));
    words.forEach((w, i) => setTimeout(() => w.classList.add('visible'), 260 + i * 130));
    dots.forEach((d, i) => setTimeout(() => d.classList.add('visible'), 340 + i * 130));

    let done = false;
    function finish() {
        if (done) return;
        done = true;
        try { sessionStorage.setItem('pl-seen', '1'); } catch (_) {}
        showCount(100);
        pl.classList.add('is-done');
        // Remove from the tree once the belts have finished retracting.
        setTimeout(() => pl.classList.add('is-gone'), 1250);
    }

    // Count up over ~1s, easing out so it feels like it's loading something.
    const START = performance.now();
    const DURATION = 1000;
    (function tick(now) {
        const t = Math.min(1, ((now || START) - START) / DURATION);
        showCount(Math.round((1 - Math.pow(1 - t, 3)) * 100));
        if (t < 1) requestAnimationFrame(tick);
        else setTimeout(finish, 160);
    })(START);

    // Escape hatches
    pl.addEventListener('click', finish);
    window.addEventListener('keydown', finish, { once: true });
    setTimeout(finish, 2600);   // hard ceiling, never trap the user
})();
