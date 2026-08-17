/**
 * 对比评测页：标准答案上传 / 逐节对比可视化 / AI 打分
 * 依赖 app.js 的 currentProjectId、chapterTitle、showToast；api.js 的 AuthToken
 */

// ===== 状态 =====
let evalChapter = null;          // 当前选中章号
let evalStandardChapters = [];   // 已上传标准答案的章号列表
const _expandedEvalSections = new Set();

function _escEval(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 通用 JSON 请求（evaluation.js 内部用，沿用 AuthToken 鉴权约定） */
async function _evalJson(path, options = {}) {
    const resp = await fetch(API_BASE + path, {
        headers: Object.assign({}, AuthToken.headers(),
            options.headers || (options.body ? { 'Content-Type': 'application/json' } : {})),
        method: options.method || 'GET',
        body: options.body || undefined,
    });
    if (resp.status === 401) { handleUnauthorized(); throw new Error('未登录'); }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || ('请求失败(' + resp.status + ')'));
    return data;
}

// ===== 页面进入 =====
async function evalPageEnter() {
    if (!currentProjectId) {
        document.getElementById('evalCompareBox').innerHTML =
            '<div class="card"><div class="card-body text-muted">请先在项目列表选择项目</div></div>';
        return;
    }
    await _evalLoadChapters();
    await _evalRefresh(false);
}

/** 渲染章节选择条（标题来自项目绑定模板包，沿用 chapterTitle） */
async function _evalLoadChapters() {
    const box = document.getElementById('evalChips');
    const chapters = (PACK_CHAPTERS && PACK_CHAPTERS.length)
        ? PACK_CHAPTERS
        : Object.keys(CHAPTER_TITLES || {}).map(n => ({ n: Number(n), title: CHAPTER_TITLES[n] }));
    if (!evalChapter || !chapters.find(c => c.n === evalChapter)) {
        evalChapter = chapters.length ? chapters[0].n : null;
    }
    box.innerHTML = chapters.map(c => `
        <div class="eval-chip ${c.n === evalChapter ? 'active' : ''}"
             onclick="evalSelectChapter(${c.n})">
            <span class="eval-chip-n">${_escEval(c.n)}</span>
            <span class="eval-chip-t">${_escEval(c.title)}</span>
            <span class="eval-chip-badge" id="evalBadge${c.n}" style="display:none">✓</span>
        </div>`).join('');
}

function evalSelectChapter(n) {
    evalChapter = n;
    _expandedEvalSections.clear();
    _evalLoadChapters();
    _evalRefresh(false);
}

/** 刷新标准答案状态 + 已有打分 + 对比缓存 */
async function _evalRefresh(showLoading) {
    const n = evalChapter;
    if (!n) return;
    const infoBox = document.getElementById('evalStdInfo');
    try {
        const std = await _evalJson(`/eval/standards?project_id=${encodeURIComponent(currentProjectId)}`);
        evalStandardChapters = std.chapters || [];
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    // 各章角标
    evalStandardChapters.forEach(c => {
        const b = document.getElementById('evalBadge' + c);
        if (b) b.style.display = '';
    });

    const hasStd = evalStandardChapters.includes(n);
    document.getElementById('evalRemoveBtn').style.display = hasStd ? '' : 'none';
    infoBox.style.display = hasStd ? '' : 'none';
    infoBox.innerHTML = hasStd
        ? `<span class="badge badge-success">第${n}章已上传标准答案</span>`
        : '';

    // 已有打分：直接展示最近一次
    const scoreBox = document.getElementById('evalScoreCard');
    try {
        const s = await _evalJson(`/eval/scores/${n}?project_id=${encodeURIComponent(currentProjectId)}`);
        const list = s.scores || [];
        if (list.length) {
            _evalRenderScore(list[list.length - 1], list.length);
        } else {
            scoreBox.style.display = 'none';
            scoreBox.innerHTML = '';
        }
    } catch (e) { /* 打分历史读失败不阻塞 */ }

    // 已有对比缓存：静默展示
    const cmpBox = document.getElementById('evalCompareBox');
    if (!hasStd) {
        cmpBox.innerHTML = '<div class="card"><div class="card-body text-muted">尚未上传本章标准答案，上传后即可逐节对比。</div></div>';
        return;
    }
    if (showLoading) {
        cmpBox.innerHTML = '<div class="card"><div class="card-body text-muted">正在逐节对齐比对…</div></div>';
    }
    try {
        const cmp = await _evalJson(`/eval/compare/${n}?project_id=${encodeURIComponent(currentProjectId)}`);
        _evalRenderCompare(cmp);
    } catch (e) {
        cmpBox.innerHTML = `<div class="card"><div class="card-body text-muted">${_escEval(e.message)}</div></div>`;
    }
}

// ===== 上传 / 删除标准答案 =====
async function evalUploadStandard(input) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    const n = evalChapter;
    const fd = new FormData();
    fd.append('file', file);
    showToast('正在上传并解析标准答案…', 'info');
    try {
        const resp = await fetch(
            `${API_BASE}/eval/standard/${n}/upload?project_id=${encodeURIComponent(currentProjectId)}`,
            { method: 'POST', headers: AuthToken.headers(), body: fd });
        if (resp.status === 401) { handleUnauthorized(); return; }
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || '上传失败');
        showToast(`标准答案已就绪：识别到 ${data.section_count} 个小节`, 'success');
        _expandedEvalSections.clear();
        await _evalRefresh(false);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function evalRemoveStandard() {
    const n = evalChapter;
    if (!confirm(`移除第${n}章标准答案（连同对比与打分记录）？`)) return;
    try {
        await _evalJson(`/eval/standard/${n}?project_id=${encodeURIComponent(currentProjectId)}`, { method: 'DELETE' });
        showToast('已移除', 'success');
        await _evalRefresh(false);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ===== 对比 =====
async function evalRunCompare(force) {
    const n = evalChapter;
    const btn = document.getElementById('evalCompareBtn');
    btn.disabled = true; btn.textContent = '对比中…';
    try {
        const cmp = await _evalJson(
            `/eval/compare/${n}?project_id=${encodeURIComponent(currentProjectId)}${force ? '&force=1' : ''}`);
        _evalRenderCompare(cmp);
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false; btn.textContent = '逐节对比';
    }
}

function _simBadge(sim) {
    if (sim >= 0.85) return '<span class="badge badge-success">高度一致</span>';
    if (sim >= 0.6) return '<span class="badge badge-warning">部分一致</span>';
    return '<span class="badge badge-error">差异较大</span>';
}

function _evalRenderCompare(cmp) {
    const box = document.getElementById('evalCompareBox');
    const s = cmp.summary || {};
    const rows = (cmp.sections || []).map((sec, i) => {
        const statusBadge = sec.status === 'matched'
            ? _simBadge(sec.similarity)
            : (sec.status === 'std_only'
                ? '<span class="badge badge-error">生成稿缺失</span>'
                : '<span class="badge badge-extra">生成稿多出</span>');
        const simBar = sec.status === 'matched'
            ? `<div class="sim-bar"><div class="sim-bar-fill" style="width:${Math.round(sec.similarity * 100)}%"></div></div>
               <span class="text-sm text-muted">${Math.round(sec.similarity * 100)}%</span>`
            : '';
        const open = _expandedEvalSections.has(i);
        const title = sec.status === 'std_only' ? sec.std_title : (sec.gen_title || sec.std_title || '（章内正文）');
        return `
        <div class="eval-sec ${sec.status}">
            <div class="eval-sec-head" onclick="evalToggleSection(${i})">
                <span class="eval-sec-arrow">${open ? '▾' : '▸'}</span>
                <span class="eval-sec-title">${_escEval(title)}</span>
                <span class="eval-sec-right">${statusBadge}${simBar}</span>
            </div>
            ${open ? `
            <div class="eval-sec-body">
                <div class="eval-pane">
                    <div class="eval-pane-head">生成稿 <span class="text-muted">（${sec.gen_chars}字${sec.gen_truncated ? '，已截断' : ''}）</span></div>
                    <pre class="eval-pane-text">${_escEval(sec.gen_text) || '<span class="text-muted">（无内容）</span>'}</pre>
                </div>
                <div class="eval-pane eval-pane-std">
                    <div class="eval-pane-head">标准答案 <span class="text-muted">（${sec.std_chars}字${sec.std_truncated ? '，已截断' : ''}）</span></div>
                    <pre class="eval-pane-text">${_escEval(sec.std_text) || '<span class="text-muted">（无内容）</span>'}</pre>
                </div>
            </div>` : ''}
        </div>`;
    }).join('');

    box.innerHTML = `
    <div class="card">
        <div class="card-header"><h3>逐节对比结果</h3>
            <button class="btn btn-ghost btn-sm" onclick="evalRunCompare(true)">重新对比</button>
        </div>
        <div class="card-body">
            <div class="eval-metrics">
                <div class="eval-metric"><div class="eval-metric-v">${Math.round((s.coverage || 0) * 100)}%</div><div class="eval-metric-l">小节覆盖率（${s.matched}/${s.std_sections}）</div></div>
                <div class="eval-metric"><div class="eval-metric-v">${Math.round((s.avg_similarity || 0) * 100)}%</div><div class="eval-metric-l">平均相似度</div></div>
                <div class="eval-metric"><div class="eval-metric-v">${s.std_only || 0}</div><div class="eval-metric-l">缺失小节</div></div>
                <div class="eval-metric"><div class="eval-metric-v">${s.gen_only || 0}</div><div class="eval-metric-l">多出小节</div></div>
            </div>
            ${rows}
        </div>
    </div>`;
}

function evalToggleSection(i) {
    if (_expandedEvalSections.has(i)) _expandedEvalSections.delete(i);
    else _expandedEvalSections.add(i);
    evalRunCompare(false);   // 复用缓存，仅重渲染
}

// ===== AI 打分 =====
async function evalRunScore() {
    const n = evalChapter;
    const btn = document.getElementById('evalScoreBtn');
    btn.disabled = true; btn.textContent = 'AI 评分中…';
    showToast('AI 评分已启动，约需30-60秒，期间请勿切换页面…', 'info');
    try {
        const score = await _evalJson(
            `/eval/score/${n}?project_id=${encodeURIComponent(currentProjectId)}`, { method: 'POST' });
        const s = await _evalJson(`/eval/scores/${n}?project_id=${encodeURIComponent(currentProjectId)}`);
        _evalRenderScore(score, (s.scores || []).length);
        // 打分卡同步刷新对比面板，并滚动到打分结果，避免“点了没反应”的观感
        await _evalRefresh(false);
        showToast(`评分完成：总分 ${score.total}`, 'success');
        document.getElementById('evalScoreCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        btn.disabled = false; btn.textContent = 'AI 打分';
    }
}

function _scoreColor(v) {
    if (v >= 85) return '#34c759';
    if (v >= 70) return '#ff9f0a';
    return '#ff3b30';
}

function _evalRenderScore(score, histCount) {
    const box = document.getElementById('evalScoreCard');
    box.style.display = '';
    const dims = (score.dimensions || []).map(d => {
        const v = Number(d.score || 0);
        return `
        <div class="eval-dim">
            <div class="eval-dim-head">
                <span>${_escEval(d.name)} <span class="text-muted text-sm">（权重${Math.round((d.weight || 0) * 100)}%）</span></span>
                <b style="color:${_scoreColor(v)}">${v}</b>
            </div>
            <div class="sim-bar"><div class="sim-bar-fill" style="width:${v}%;background:${_scoreColor(v)}"></div></div>
            ${d.comment ? `<div class="text-sm text-muted" style="margin-top:4px">${_escEval(d.comment)}</div>` : ''}
        </div>`;
    }).join('');
    const missing = (score.missing_points || []).map(m => `<li>${_escEval(m)}</li>`).join('');
    const nobasis = (score.no_basis_points || []).map(m => `<li>${_escEval(m)}</li>`).join('');
    box.innerHTML = `
    <div class="card">
        <div class="card-header"><h3>AI 打分（${_escEval(score.created_at || '')}，模型 ${_escEval(score.model || '')}${histCount > 1 ? `，共${histCount}次记录` : ''}）</h3></div>
        <div class="card-body">
            <div class="eval-total" style="border-color:${_scoreColor(score.total)}">
                <div class="eval-total-v" style="color:${_scoreColor(score.total)}">${score.total}</div>
                <div class="eval-total-l">综合得分 / 100</div>
            </div>
            ${dims}
            ${score.summary ? `<div class="eval-summary"><b>总评：</b>${_escEval(score.summary)}</div>` : ''}
            ${missing ? `<div class="eval-missing"><b>缺失要点（有据可依）：</b><ul>${missing}</ul></div>` : ''}
            ${nobasis ? `<div class="eval-missing" style="color:var(--text-muted,#888)"><b>无依据要点（不计扣分）：</b><ul>${nobasis}</ul></div>` : ''}
        </div>
    </div>`;
}
