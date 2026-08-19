/**
 * 对比评测页：标准答案上传 / 逐节对比可视化 / AI 打分
 * 依赖 app.js 的 currentProjectId、chapterTitle、showToast；api.js 的 AuthToken
 */

// ===== 状态 =====
let evalMode = 'chapter';        // chapter | section —— 业务反馈需要能只对比/打分一个小节
let evalChapter = null;          // 当前选中章号（chapter 模式）
let evalSection = null;          // 当前选中小节 {id, chapter_n, title}（section 模式）
let _evalSkillSections = [];     // 已配置小节级 Know-how 的小节清单
let evalStandardChapters = [];   // 已上传标准答案的章号列表
let evalGeneratedChapters = [];  // 已生成内容的章号列表
const _expandedEvalSections = new Set();

/** 当前对比/打分作用域：章模式用整章接口，节模式用 /section 变体——标准答案上传/删除
 * 始终按章（上传的就是整章 docx），对比/打分按当前模式收窄。 */
function _evalScope() {
    if (evalMode === 'section' && evalSection) {
        return {
            n: evalSection.chapter_n, suffix: '/section',
            qs: `&section_title=${encodeURIComponent(evalSection.title)}`,
        };
    }
    return { n: evalChapter, suffix: '', qs: '' };
}

function evalSetMode(mode) {
    if (mode === evalMode) return;
    evalMode = mode;
    _expandedEvalSections.clear();
    _evalLoadChapters();
    _evalRefresh(false);
}

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
    try { _evalSkillSections = (await API.listSkillSections()).sections || []; } catch (_) { _evalSkillSections = []; }
    await _evalLoadChapters();
    await _evalRefresh(false);
}

/** 渲染选择条：章模式列大章节（标题来自项目绑定模板包），节模式列已配置小节级
 * Know-how 的小节——两种粒度共用同一套对比/打分 UI，只是作用域不同。 */
async function _evalLoadChapters() {
    const box = document.getElementById('evalChips');
    const modeBar = `<div class="sk-seg" style="margin-bottom:10px">
        <div class="sk-seg-item ${evalMode === 'chapter' ? 'active' : ''}" onclick="evalSetMode('chapter')">按章节</div>
        <div class="sk-seg-item ${evalMode === 'section' ? 'active' : ''}" onclick="evalSetMode('section')">按小节</div>
    </div>`;
    if (evalMode === 'section') {
        if (!evalSection || !_evalSkillSections.find(s => s.id === evalSection.id)) {
            const first = _evalSkillSections[0];
            evalSection = first ? { id: first.id, chapter_n: first.chapter_n, title: first.title } : null;
        }
        box.innerHTML = modeBar + (_evalSkillSections.length ? _evalSkillSections.map(s => `
        <div class="eval-chip ${evalSection && s.id === evalSection.id ? 'active' : ''}"
             onclick="evalSelectSection('${s.id}')" title="${s.generated ? '已生成' : '未生成'}">
            <span class="eval-chip-n">${_escEval(s.id)}</span>
            <span class="eval-chip-t">${_escEval(s.title)}</span>
            <span class="eval-chip-dot ${s.generated ? '' : 'off'}"></span>
            <span class="eval-chip-badge" id="evalBadgeSec${_escEval(s.id).replace('.', '_')}" style="display:none">✓</span>
        </div>`).join('') : '<div class="text-muted text-sm" style="padding:8px 0">暂无已配置 Know-how 的小节</div>');
        return;
    }
    const chapters = (PACK_CHAPTERS && PACK_CHAPTERS.length)
        ? PACK_CHAPTERS
        : Object.keys(CHAPTER_TITLES || {}).map(n => ({ n: Number(n), title: CHAPTER_TITLES[n] }));
    if (!evalChapter || !chapters.find(c => c.n === evalChapter)) {
        evalChapter = chapters.length ? chapters[0].n : null;
    }
    box.innerHTML = modeBar + chapters.map(c => {
        const gen = evalGeneratedChapters.includes(c.n);
        return `
        <div class="eval-chip ${c.n === evalChapter ? 'active' : ''}"
             onclick="evalSelectChapter(${c.n})" title="${gen ? '已生成' : '未生成'}">
            <span class="eval-chip-n">${_escEval(c.n)}</span>
            <span class="eval-chip-t">${_escEval(c.title)}</span>
            <span class="eval-chip-dot ${gen ? '' : 'off'}"></span>
            <span class="eval-chip-badge" id="evalBadge${c.n}" style="display:none">✓</span>
        </div>`;
    }).join('');
}

function evalSelectChapter(n) {
    evalChapter = n;
    _expandedEvalSections.clear();
    _evalLoadChapters();
    _evalRefresh(false);
}

function evalSelectSection(sectionId) {
    const s = _evalSkillSections.find(x => x.id === sectionId);
    if (!s) return;
    evalSection = { id: s.id, chapter_n: s.chapter_n, title: s.title };
    _expandedEvalSections.clear();
    _evalLoadChapters();
    _evalRefresh(false);
}

/** 刷新标准答案状态 + 已有打分 + 对比缓存；章/节两种模式共用，区别只在作用域和
 * "是否已生成"的判断口径（节模式看这一节自己是否生成，不是看整章）。 */
async function _evalRefresh(showLoading) {
    const scope = _evalScope();
    const n = scope.n;
    if (!n) return;
    const infoBox = document.getElementById('evalStdInfo');
    try {
        const std = await _evalJson(`/eval/standards?project_id=${encodeURIComponent(currentProjectId)}`);
        evalStandardChapters = std.chapters || [];
        evalGeneratedChapters = std.generated || [];
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    // 各章/节角标（绿点=已生成，✓=已传标准答案——标准答案始终按章上传，节模式复用同一份）
    _evalLoadChapters();
    evalStandardChapters.forEach(c => {
        const b = document.getElementById('evalBadge' + c);
        if (b) b.style.display = '';
        if (evalMode === 'section') {
            _evalSkillSections.filter(s => s.chapter_n === c).forEach(s => {
                const bs = document.getElementById('evalBadgeSec' + s.id.replace('.', '_'));
                if (bs) bs.style.display = '';
            });
        }
    });

    const hasStd = evalStandardChapters.includes(n);
    const gen = evalMode === 'section'
        ? !!(evalSection && (_evalSkillSections.find(s => s.id === evalSection.id) || {}).generated)
        : evalGeneratedChapters.includes(n);
    const scopeLabel = evalMode === 'section' ? `小节 ${evalSection.id}` : `第${n}章`;
    document.getElementById('evalRemoveBtn').style.display = hasStd ? '' : 'none';
    infoBox.style.display = hasStd ? '' : 'none';
    infoBox.innerHTML = hasStd
        ? `<span class="badge badge-success">第${n}章已上传标准答案${evalMode === 'section' ? '（含全部小节，本节从中比对）' : ''}</span>`
        : '';

    // 入口状态：未生成的不能对比/打分，避免“都能打分”的误导
    const scoreBtn = document.getElementById('evalScoreBtn');
    const cmpBtn = document.getElementById('evalCompareBtn');
    cmpBtn.disabled = !gen;
    if (!gen) {
        scoreBtn.disabled = true; scoreBtn.textContent = evalMode === 'section' ? '本节未生成' : '本章未生成';
    } else {
        scoreBtn.disabled = false; scoreBtn.textContent = 'AI 打分';
    }

    // 打分卡：仅已生成且有历史时展示
    const scoreBox = document.getElementById('evalScoreCard');
    if (!gen) {
        scoreBox.style.display = 'none';
        scoreBox.innerHTML = '';
    } else {
        try {
            const s = await _evalJson(`/eval/scores/${n}${scope.suffix}?project_id=${encodeURIComponent(currentProjectId)}${scope.qs}`);
            const list = s.scores || [];
            if (list.length) {
                _evalRenderScore(list[list.length - 1], list.length, scopeLabel);
            } else {
                scoreBox.style.display = 'none';
                scoreBox.innerHTML = '';
            }
        } catch (e) { /* 打分历史读失败不阻塞 */ }
    }

    // 对比面板
    const cmpBox = document.getElementById('evalCompareBox');
    if (!hasStd) {
        cmpBox.innerHTML = '<div class="card"><div class="card-body text-muted">尚未上传本章标准答案，上传后即可逐节对比。</div></div>';
        return;
    }
    if (!gen) {
        cmpBox.innerHTML = `<div class="card"><div class="card-body text-muted">${_escEval(scopeLabel)}内容尚未生成，请先到生成页生成后再对比打分。</div></div>`;
        return;
    }
    if (showLoading) {
        cmpBox.innerHTML = '<div class="card"><div class="card-body text-muted">正在逐节对齐比对…</div></div>';
    }
    try {
        const cmp = await _evalJson(`/eval/compare/${n}${scope.suffix}?project_id=${encodeURIComponent(currentProjectId)}${scope.qs}`);
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
    const n = _evalScope().n;   // 上传的是整章 docx（本身就含各小节），节模式下按该节所属章上传
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
    const n = _evalScope().n;   // 标准答案始终按章存，节模式下删的也是这一节所属那整章的标准答案
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
    const scope = _evalScope();
    const btn = document.getElementById('evalCompareBtn');
    btn.disabled = true; btn.textContent = '对比中…';
    try {
        const cmp = await _evalJson(
            `/eval/compare/${scope.n}${scope.suffix}?project_id=${encodeURIComponent(currentProjectId)}${scope.qs}${force ? '&force=1' : ''}`);
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
    const scope = _evalScope();
    const scopeLabel = evalMode === 'section' ? `小节 ${evalSection.id}` : `第${scope.n}章`;
    const btn = document.getElementById('evalScoreBtn');
    btn.disabled = true; btn.textContent = 'AI 评分中…';
    showToast('AI 评分已启动，约需30-60秒，期间请勿切换页面…', 'info');
    try {
        const score = await _evalJson(
            `/eval/score/${scope.n}${scope.suffix}?project_id=${encodeURIComponent(currentProjectId)}${scope.qs}`, { method: 'POST' });
        const s = await _evalJson(`/eval/scores/${scope.n}${scope.suffix}?project_id=${encodeURIComponent(currentProjectId)}${scope.qs}`);
        _evalRenderScore(score, (s.scores || []).length, scopeLabel);
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

function _evalRenderScore(score, histCount, scopeLabel) {
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
        <div class="card-header"><h3>AI 打分 · ${_escEval(scopeLabel || `第${score.chapter || evalChapter || ''}章`)}${score.section_title ? `（${_escEval(score.section_title)}）` : ''}（${_escEval(score.created_at || '')}，模型 ${_escEval(score.model || '')}${histCount > 1 ? `，共${histCount}次记录` : ''}）</h3></div>
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
