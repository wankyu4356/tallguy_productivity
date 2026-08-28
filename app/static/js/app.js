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
   Motion layer II (ref: trionn.com)

   Scroll hairline, headline character reveal, staggered lists, magnetic
   buttons, card tilt and the belt wipe on outbound navigation.

   Rules this module holds itself to, because it is pure decoration:
     - prefers-reduced-motion switches the whole thing off before anything runs
     - no element is ever left hidden: every reveal has a path to visible, and
       anything already on screen at load is shown outright
     - navigation is never blocked on an animation finishing
   ========================================================================== */
(function () {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) return;

    const raf = window.requestAnimationFrame.bind(window);
    const ready = (fn) => (document.readyState === 'loading'
        ? document.addEventListener('DOMContentLoaded', fn) : fn());

    /* ---- Scroll progress hairline ------------------------------------- */
    function scrollBar() {
        const bar = document.createElement('div');
        bar.className = 'sx';
        document.body.appendChild(bar);
        let queued = false;
        const paint = () => {
            queued = false;
            const max = document.documentElement.scrollHeight - window.innerHeight;
            const p = max > 0 ? Math.min(1, window.scrollY / max) : 0;
            bar.style.transform = `scaleX(${p})`;
        };
        addEventListener('scroll', () => {
            if (queued) return;
            queued = true;
            raf(paint);
        }, { passive: true });
        paint();
    }

    /* ---- Headline: split into lines, then characters ------------------- */
    function splitHeadline() {
        const h1 = document.querySelector('.container h1');
        // Only plain text and <br> can be split safely — bail on anything else
        // rather than destroying markup we didn't author.
        if (!h1 || h1.dataset.split) return;
        const ok = Array.from(h1.childNodes).every(
            n => n.nodeType === 3 || (n.nodeType === 1 && n.tagName === 'BR'));
        if (!ok) return;

        const lines = h1.innerHTML.split(/<br\s*\/?>/i);
        h1.dataset.split = '1';
        // Per-character spans read out letter by letter, so hand assistive
        // tech the whole heading and hide the pieces.
        h1.setAttribute('aria-label', h1.textContent.replace(/\s+/g, ' ').trim());
        h1.innerHTML = '';
        let i = 0;
        lines.forEach((line) => {
            const wrap = document.createElement('span');
            wrap.className = 'hl-line';
            wrap.setAttribute('aria-hidden', 'true');
            // textContent round-trip: the split source was text + <br> only,
            // so this also unescapes entities without ever parsing HTML.
            const probe = document.createElement('textarea');
            probe.innerHTML = line;
            Array.from(probe.value).forEach((ch) => {
                const c = document.createElement('span');
                c.className = 'hl-char';
                c.textContent = ch;
                c.style.setProperty('--d', `${0.18 + i * 0.018}s`);
                wrap.appendChild(c);
                i += 1;
            });
            h1.appendChild(wrap);
        });
        const go = () => raf(() => raf(() => h1.classList.add('hl-ready')));
        // If the splash is still up, let it hand over; never wait forever.
        const pl = document.getElementById('pl');
        if (pl && !pl.classList.contains('is-gone')) {
            let started = false;
            const once = () => { if (!started) { started = true; go(); } };
            document.addEventListener('pl:done', once, { once: true });
            setTimeout(once, 3000);
        } else {
            go();
        }
    }

    /* ---- Staggered children -------------------------------------------- */
    function stagger() {
        const groups = document.querySelectorAll(
            '.chips, .list, .crawl-pipeline, .ri-nav, .rail, .action-bar, .steps');
        if (!groups.length) return;
        const io = new IntersectionObserver((entries) => {
            entries.forEach((e) => {
                if (!e.isIntersecting) return;
                e.target.classList.add('is-in');
                io.unobserve(e.target);
            });
        }, { rootMargin: '0px 0px -6% 0px', threshold: 0.05 });

        groups.forEach((g) => {
            const kids = Array.from(g.children);
            if (!kids.length || kids.length > 40) return;   // long lists: no
            // A group inside a display:none subtree (the crawl tracker starts
            // hidden) must not have its children pre-hidden — if the observer
            // never fires we'd have made them permanently invisible.
            if (g.offsetParent === null && getComputedStyle(g).position !== 'fixed') return;
            g.setAttribute('data-stagger', '');
            kids.forEach((k, i) => k.style.setProperty('--d', `${i * 0.05}s`));
            io.observe(g);
        });
    }

    /* ---- Magnetic buttons ---------------------------------------------- */
    function magnetic() {
        const btns = document.querySelectorAll('.btn-primary, .btn-lg');
        btns.forEach((b) => {
            b.classList.add('mag');
            b.addEventListener('pointermove', (ev) => {
                const r = b.getBoundingClientRect();
                const dx = (ev.clientX - (r.left + r.width / 2)) / r.width;
                const dy = (ev.clientY - (r.top + r.height / 2)) / r.height;
                b.classList.add('is-pulled');
                b.style.transform = `translate(${dx * 10}px, ${dy * 6}px)`;
            });
            b.addEventListener('pointerleave', () => {
                b.classList.remove('is-pulled');
                b.style.transform = '';
            });
        });
    }

    /* ---- Card tilt ------------------------------------------------------ */
    function tilt() {
        // Only cards that aren't scroll containers or drag surfaces.
        const cards = document.querySelectorAll('.home-grid > .card');
        cards.forEach((c) => {
            c.classList.add('tilt');
            c.addEventListener('pointermove', (ev) => {
                const r = c.getBoundingClientRect();
                const px = (ev.clientX - r.left) / r.width - 0.5;
                const py = (ev.clientY - r.top) / r.height - 0.5;
                c.classList.add('is-tilting');
                c.style.transform =
                    `perspective(1100px) rotateX(${-py * 2.4}deg) rotateY(${px * 2.4}deg)`;
                // feeds the existing pointer-spotlight ::after
                c.style.setProperty('--mx', `${ev.clientX - r.left}px`);
                c.style.setProperty('--my', `${ev.clientY - r.top}px`);
            });
            c.addEventListener('pointerleave', () => {
                c.classList.remove('is-tilting');
                c.style.transform = '';
            });
        });
    }

    /* ---- Belt wipe on outbound navigation ------------------------------ */
    function pageWipe() {
        const wipe = document.createElement('div');
        wipe.className = 'wipe';
        wipe.innerHTML = '<i></i><i></i><i></i><i></i><i></i><i></i>';
        document.body.appendChild(wipe);

        function lift() { wipe.classList.remove('is-on', 'is-closing'); }

        document.addEventListener('click', (ev) => {
            // An inline onclick may already have cancelled this navigation
            // (result.html guards its back-links that way) — respect it.
            if (ev.defaultPrevented) return;
            const a = ev.target.closest && ev.target.closest('a[href]');
            if (!a) return;
            const href = a.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('mailto:')) return;
            if (a.target === '_blank' || a.hasAttribute('download')) return;
            if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
            let url;
            try { url = new URL(a.href, location.href); } catch (_) { return; }
            if (url.origin !== location.origin) return;
            if (url.pathname === location.pathname && url.search === location.search) return;
            // /api/* serves file downloads: the page never unloads, so a
            // curtain dropped over it would never lift again.
            if (url.pathname.startsWith('/api/')) return;

            ev.preventDefault();
            wipe.classList.add('is-on');
            raf(() => wipe.classList.add('is-closing'));
            // Navigate on a timer, not on transitionend: a dropped transition
            // event must never strand the user behind a black curtain.
            setTimeout(() => { location.href = url.href; }, 430);
            // Last-resort net: if we're somehow still here, uncover the page.
            setTimeout(() => { if (!document.hidden) lift(); }, 3000);
        });

        // Coming back via bfcache would otherwise show the curtain still down.
        addEventListener('pageshow', (e) => { if (e.persisted) lift(); });
    }

    ready(() => {
        scrollBar();
        splitHeadline();
        stagger();
        magnetic();
        tilt();
        pageWipe();
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

    /* --- Plus marks fly in from the four screen corners ------------------
       They're fixed-position clones that travel to the logo box's corners;
       the box's own marks only fade up once the travellers have landed. */
    const wrap = pl.querySelector('.pl-logo-wrap');
    const fliers = [];
    if (wrap) {
        const r = wrap.getBoundingClientRect();
        const dest = [
            [r.left - 7,  r.top - 7],
            [r.right + 7, r.top - 7],
            [r.left - 7,  r.bottom + 7],
            [r.right + 7, r.bottom + 7],
        ];
        const from = [
            [24, 24],
            [innerWidth - 24, 24],
            [24, innerHeight - 24],
            [innerWidth - 24, innerHeight - 24],
        ];
        dest.forEach((d, i) => {
            const f = document.createElement('span');
            f.className = 'pl-fly';
            f.textContent = '+';
            f.style.left = from[i][0] + 'px';
            f.style.top = from[i][1] + 'px';
            document.body.appendChild(f);
            fliers.push(f);
            requestAnimationFrame(() => requestAnimationFrame(() => {
                f.style.left = d[0] + 'px';
                f.style.top = d[1] + 'px';
            }));
        });
        // Hand off to the in-place marks once the travel is over.
        setTimeout(() => {
            wrap.classList.add('is-landed');
            fliers.forEach(f => { f.style.opacity = '0'; });
            setTimeout(() => fliers.forEach(f => f.remove()), 400);
        }, 880);
    }

    /* --- Exit: the logo box travels into the header's logo mark ---------- */
    function morphToHeader() {
        const box = pl.querySelector('.pl-logo-box');
        const boxImg = box && box.querySelector('img');
        const navImg = document.querySelector('header .logo img');
        if (!box || !boxImg || !navImg) return false;

        const from = box.getBoundingClientRect();
        const to = navImg.getBoundingClientRect();
        if (!to.width || !to.height) return false;   // header not laid out

        // Re-parent to <body> first: .pl.is-done fades .pl-center out, and an
        // ancestor's opacity would take the travelling box with it.
        document.body.appendChild(box);
        // Freeze the box where it already is, then let CSS transition it.
        box.style.left = from.left + 'px';
        box.style.top = from.top + 'px';
        box.style.width = from.width + 'px';
        box.style.height = from.height + 'px';
        box.classList.add('is-morphing');
        navImg.classList.add('is-handoff');

        requestAnimationFrame(() => requestAnimationFrame(() => {
            const pad = (from.width - boxImg.getBoundingClientRect().width) / 2;
            box.style.left = (to.left - pad) + 'px';
            box.style.top = (to.top - pad) + 'px';
            box.style.width = (to.width + pad * 2) + 'px';
            box.style.height = (to.height + pad * 2) + 'px';
            box.style.borderColor = 'transparent';
            box.style.background = 'transparent';
            boxImg.style.width = to.width + 'px';
            boxImg.style.height = to.height + 'px';
            boxImg.style.boxShadow = 'none';
        }));

        // Reveal the real header mark and drop the traveller.
        setTimeout(() => {
            navImg.classList.remove('is-handoff');
            box.remove();
        }, 820);
        return true;
    }

    let done = false;
    function finish() {
        if (done) return;
        done = true;
        try { sessionStorage.setItem('pl-seen', '1'); } catch (_) {}
        showCount(100);
        fliers.forEach(f => f.remove());
        const morphing = morphToHeader();
        pl.classList.add('is-done');
        // Let the page's own entrance animations start as the belts lift,
        // instead of playing out unseen behind them.
        document.dispatchEvent(new CustomEvent('pl:done'));
        // Remove from the tree once the belts have finished retracting.
        // The morph outlives the belts, so hold the wrapper a little longer.
        setTimeout(() => pl.classList.add('is-gone'), morphing ? 900 : 1250);
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
