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
}

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById(`tab-${tabName}`).style.display = 'block';
}
