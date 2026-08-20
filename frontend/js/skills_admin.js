/**
 * Skill 管理页：预览/编辑模板包内各章写作要求、全局总纲、写作排版要求。
 * 用户修改存数据卷覆盖层（不污染代码默认），可随时重置回默认。
 * 依赖 app.js 的 showToast；api.js 的 AuthToken / API_BASE
 */

let SK_PACK_ID = null;
let SK_FILES = [];          // [{rel,label,kind,n,overridden}]
let SK_CURRENT = null;      // 当前 rel
let SK_DATA = null;         // {content, default_content, overridden, label}
let SK_MODE = 'preview';    // preview | edit
let SK_DIRTY = false;
let SK_RECOMPILE = null;    // { ok, errors, preview } — 最近一次预览结果，未应用

function _skEsc(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function _skJson(path, options = {}) {
    const resp = await fetch(API_BASE + path, {
        method: options.method || 'GET',
        headers: Object.assign({}, AuthToken.headers(),
            options.json ? { 'Content-Type': 'application/json' } : {}),
        body: options.json ? JSON.stringify(options.json) : undefined,
    });
    if (resp.status === 401) { handleUnauthorized(); throw new Error('未登录'); }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || ('请求失败(' + resp.status + ')'));
    return data;
}

// ===== 页面进入 =====
async function skillsPageEnter() {
    try {
        const packs = await _skJson('/packs');
        SK_PACK_ID = packs.default_id || ((packs.packs || [])[0] || {}).id;
        if (!SK_PACK_ID) throw new Error('没有可用模板包');
        const list = await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skills`);
        SK_FILES = list.skills || [];
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    // 默认选中项排除已从列表隐藏的 chapter 项（见 _skRenderList），否则会选中一个看不见的条目。
    const visible = SK_FILES.filter(f => f.kind !== 'chapter' && f.kind !== 'section');
    if (!SK_CURRENT || !visible.find(f => f.rel === SK_CURRENT)) {
        SK_CURRENT = visible.length ? visible[0].rel : null;
    }
    _skRenderList();
    await skLoadCurrent();
}

function _skRenderList() {
    // 整章写作要求（原 reading/ch{n}.md）已从管理页移除——小节生产线已取代整章写法，
    // 这批文本只作为未来拆分成小节 Know-how 的原始素材保留在磁盘上，不在此处展示/编辑。
    const box = document.getElementById('skList');
    const globals = SK_FILES.filter(f => f.kind === 'global');
    const compiled = SK_FILES.filter(f => String(f.kind || '').startsWith('compiled_'));
    const item = f => `
        <div class="sk-item ${f.rel === SK_CURRENT ? 'active' : ''}" onclick="skSelect('${_skEsc(f.rel)}')">
            <span class="sk-item-label">${_skEsc(f.label)}</span>
            ${f.kind === 'section' && !f.configured ? '<span class="badge badge-extra" title="尚无小节级生产管线，暂不影响生成，可先写 Know-how 草稿">未配置</span>' : ''}
            ${f.overridden ? '<span class="sk-dot" title="已被用户修改"></span>' : ''}
        </div>`;
    box.innerHTML = `
        <div class="sk-group-title">小节可执行三件套</div>
        ${compiled.map(item).join('')}
        <div class="sk-group-title">全局</div>
        ${globals.map(item).join('')}`;
}

async function skSelect(rel) {
    if (SK_DIRTY && !confirm('当前修改尚未保存，切换将丢失，确定切换？')) return;
    SK_CURRENT = rel;
    SK_DIRTY = false;
    SK_RECOMPILE = null;
    _skRenderList();
    await skLoadCurrent();
}

async function skLoadCurrent() {
    if (!SK_CURRENT) return;
    const box = document.getElementById('skMain');
    box.innerHTML = '<div class="card"><div class="card-body text-muted">加载中…</div></div>';
    try {
        SK_DATA = await _skJson(
            `/packs/${encodeURIComponent(SK_PACK_ID)}/skill?rel=${encodeURIComponent(SK_CURRENT)}`);
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    SK_DIRTY = false;
    _skRenderMain();
}

function skSetMode(mode) {
    if (mode === SK_MODE) return;
    SK_MODE = mode;
    _skRenderMain();
}

function _skRecompileBtn() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT);
    if (!f || f.kind !== 'section') return '';
    return '<button class="btn btn-ghost btn-sm" onclick="skRecompile()" title="把当前 Know-how 原文编译为抽取规则/生成模板/审核清单预览，不会直接生效">AI 重新编译</button>';
}

function _skEditHint() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT) || {};
    if (f.kind === 'section') return '保存的是业务 Know-how 原文；点击“AI 重新编译”并确认应用后，才会更新三件套。';
    if (f.kind === 'compiled_extraction') return '这是可执行提取规则；保存后，下一次点击“提取数据”立即按新规则运行。';
    if (f.kind === 'compiled_generation') return '这是生成时实际读取的实体 SKILL：正文指导 AI 写作，文末 JSON 负责字段和结构。只改正文说明会在下次生成立即生效；修改 JSON 后需重新提取数据。';
    if (f.kind === 'compiled_audit') return '这是可执行 AI 审核 SKILL；保存后，下一次审核该小节立即生效。';
    return '修改保存后，后续生成即时按新版本执行；代码内置版本不受影响。';
}

function _skRenderRecompilePreview() {
    if (!SK_RECOMPILE) return '';
    const r = SK_RECOMPILE;
    if (!r.ok) {
        return `<div class="card" style="margin-top:12px">
            <div class="card-header"><h4>编译预览失败</h4></div>
            <div class="card-body"><ul>${(r.errors || []).map(e => `<li>${_skEsc(e)}</li>`).join('')}</ul>
            ${r.raw ? `<pre class="sk-editor" style="max-height:200px">${_skEsc(r.raw)}</pre>` : ''}</div>
        </div>`;
    }
    return `<div class="card" style="margin-top:12px">
        <div class="card-header sk-head"><h4>编译预览（尚未生效）</h4>
            <div class="flex gap-8">
                <button class="btn btn-ghost btn-sm" onclick="SK_RECOMPILE=null;_skRenderMain()">丢弃预览</button>
                <button class="btn btn-primary btn-sm" onclick="skApplyRecompile()">应用到该小节</button>
            </div>
        </div>
        <div class="card-body">
            <div class="text-muted text-sm" style="margin-bottom:8px">
                只替换 ${_skEsc(SK_CURRENT_SECTION_ID())} 自身的抽取规则/生成模板/审核清单，其余小节不受影响；应用后规则版本会追加编译时间戳，可随时在此页再次编译或联系管理员回退。
            </div>
            <pre class="sk-editor" style="max-height:420px">${_skEsc(JSON.stringify(r.preview, null, 2))}</pre>
        </div>
    </div>`;
}

function _skCurrentSectionId() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT);
    return f ? f.section_id : '';
}
function SK_CURRENT_SECTION_ID() { return _skCurrentSectionId(); }

async function skRecompile() {
    const sectionId = _skCurrentSectionId();
    if (!sectionId) return;
    const text = SK_MODE === 'edit' && document.getElementById('skEditor')
        ? document.getElementById('skEditor').value
        : (SK_DATA ? SK_DATA.content : '');
    if (!text.trim()) { showToast('Know-how 原文为空，无法编译', 'warning'); return; }
    showToast('正在编译，请稍候…');
    try {
        const resp = await API.recompileSection(sectionId, text);
        SK_RECOMPILE = resp;
        _skRenderMain();
        showToast(resp.ok ? '编译预览已生成，请核对后再应用' : '编译预览未通过校验，详情见下方', resp.ok ? 'success' : 'warning');
    } catch (e) {
        showToast('编译失败：' + e.message, 'error');
    }
}

async function skApplyRecompile() {
    const sectionId = _skCurrentSectionId();
    if (!sectionId || !SK_RECOMPILE || !SK_RECOMPILE.ok) return;
    if (!confirm(`确定把这份编译结果应用为小节 ${sectionId} 的正式规则？其余小节不受影响。`)) return;
    try {
        await API.applyRecompiledSection(sectionId, SK_RECOMPILE.preview);
        showToast('已应用，后续生成即按新规则执行', 'success');
        SK_RECOMPILE = null;
        _skRenderMain();
    } catch (e) {
        showToast('应用失败：' + e.message, 'error');
    }
}

function _skRenderMain() {
    const box = document.getElementById('skMain');
    const d = SK_DATA;
    if (!d) return;
    const statusBadge = d.overridden
        ? '<span class="badge badge-warning">已修改</span>'
        : '<span class="badge badge-info">代码默认</span>';
    box.innerHTML = `
    <div class="card">
        <div class="card-header sk-head">
            <h3>${_skEsc(d.label)} ${statusBadge}</h3>
            <div class="flex gap-8">
                <div class="sk-seg">
                    <div class="sk-seg-item ${SK_MODE === 'preview' ? 'active' : ''}" onclick="skSetMode('preview')">预览</div>
                    <div class="sk-seg-item ${SK_MODE === 'edit' ? 'active' : ''}" onclick="skSetMode('edit')">编辑</div>
                </div>
                ${d.overridden ? '<button class="btn btn-ghost btn-sm" onclick="skReset()">重置为默认</button>' : ''}
                ${_skTestRunBtn()}
                ${_skRecompileBtn()}
                <button class="btn btn-primary btn-sm" id="skSaveBtn" onclick="skSave()" style="${SK_MODE === 'edit' ? '' : 'display:none'}">保存修改</button>
            </div>
        </div>
        <div class="card-body">
            ${SK_MODE === 'edit' ? `
                <div class="text-muted text-sm" style="margin-bottom:8px">
                    ${_skEditHint()} 可随时“重置为默认”。
                </div>
                <textarea id="skEditor" class="sk-editor" oninput="skDirty()" spellcheck="false">${_skEsc(d.content)}</textarea>
            ` : `
                <div class="sk-preview md-body">${skRenderMd(d.content)}</div>
            `}
        </div>
    </div>
    ${_skRenderRecompilePreview()}`;
}

function skDirty() { SK_DIRTY = true; }

// 各章写作要求支持整章测试运行；已配置小节级管线的小节 Know-how 支持单节生成
// （planning/排版要求是跨章共性，无单章运行入口；未配置的小节 Know-how 还没有生产管线可跑）
function _skTestRunBtn() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT);
    if (!f) return '';
    if (f.kind === 'chapter' && f.n) {
        return '<button class="btn btn-ghost btn-sm" onclick="skTestRun()" title="按当前 skill 重新生成本章并跳转查看结果">测试运行</button>';
    }
    if (f.kind === 'section' && f.configured) {
        return '<button class="btn btn-ghost btn-sm" onclick="skTestRunSection()" title="按当前数据中间层重新生成本小节并跳转查看结果">生成本节</button>';
    }
    if (f.kind === 'compiled_generation') {
        return '<button class="btn btn-ghost btn-sm" onclick="skTestRunSection()" title="按当前生成 SKILL 重新生成本小节">生成本节</button>';
    }
    return '';
}

/** 小节测试运行：按当前生效的数据中间层重新生成该小节，跳到申报材料页查看结果。
 * 与 skTestRun（整章）并列，但小节生成不经过 Kimi 轮询——生成本身是同步的数据中间层渲染。 */
async function skTestRunSection() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT);
    if (!f || !f.section_id) return;
    if (!currentProjectId) {
        try {
            const ps = await API.getProjects();
            const list = Array.isArray(ps) ? ps : (ps.projects || []);
            if (list.length) currentProjectId = list[0].id;
        } catch (e) { /* 下面统一提示 */ }
    }
    if (!currentProjectId) { showToast('请先在项目列表中创建项目', 'warning'); return; }
    if (SK_DIRTY && !confirm('当前编辑尚未保存，测试运行仍按“已保存”的版本执行。确定继续？')) return;
    if (!confirm(`将重新生成小节 ${f.section_id}，该节已生成内容会被覆盖。确定运行？`)) return;
    try {
        await API.generateSkillSection(f.section_id);
        showToast(`${f.section_id} 已重新生成`, 'success');
        navigate('ndrc');
        await selectSkillSection(f.section_id);
    } catch (e) {
        showToast('生成失败: ' + e.message, 'error');
    }
}

/** 测试运行：按当前生效的 skill 重新生成该章，跳到申报材料页查看结果。
 * 生成是异步的，申报材料页会自动接入轮询并显示全局生成横幅。 */
async function skTestRun() {
    const f = SK_FILES.find(x => x.rel === SK_CURRENT);
    if (!f || !f.n) return;
    const n = f.n;
    // Skill 页不依赖项目上下文，先确保有当前项目（没有则取第一个）
    if (!currentProjectId) {
        try {
            const ps = await API.getProjects();
            const list = Array.isArray(ps) ? ps : (ps.projects || []);
            if (list.length) currentProjectId = list[0].id;
        } catch (e) { /* 下面统一提示 */ }
    }
    if (!currentProjectId) { showToast('请先在项目列表中创建项目', 'warning'); return; }
    if (SK_DIRTY && !confirm('当前编辑尚未保存，测试运行仍按“已保存”的版本执行。确定继续？')) return;
    if (!confirm(`将按当前生效的 skill 重新生成第${n}章，该章已生成内容会被覆盖。确定运行？`)) return;
    try {
        await API.runChapter(n);
    } catch (e) {
        if (!String(e.message).includes('正在生成')) {
            showToast('启动失败: ' + e.message, 'error');
            return;
        }
    }
    navigate('ndrc');
    await selectChapter(n);   // 渲染该章编辑视图并自动接入生成轮询
    showToast(`第${n}章测试生成已启动，约需数分钟`, 'success');
}

async function skSave() {
    const ta = document.getElementById('skEditor');
    if (!ta) return;
    const btn = document.getElementById('skSaveBtn');
    btn.disabled = true; btn.textContent = '保存中…';
    try {
        const current = SK_FILES.find(x => x.rel === SK_CURRENT) || {};
        await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skill/save`, {
            method: 'POST', json: { rel: SK_CURRENT, content: ta.value },
        });
        const messages = {
            section: '已保存 Know-how 原文；点击“AI 重新编译”并确认应用后才会更新三件套',
            compiled_extraction: '提取规则已保存；下一次点击“提取数据”按新规则运行',
            compiled_generation: '真实生成 SKILL 已保存；正文修改立即用于下次生成，JSON 修改后请重新提取数据',
            compiled_audit: '审核 SKILL 已保存；下一次审核本小节按新清单运行',
        };
        showToast(messages[current.kind] || '已保存，后续运行即时生效', 'success');
        SK_DIRTY = false;
        SK_RECOMPILE = null;
        const list = await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skills`);
        SK_FILES = list.skills || [];
        _skRenderList();
        await skLoadCurrent();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '保存修改'; }
    }
}

async function skReset() {
    if (!confirm('重置为代码内置默认版本？你的修改将被删除。')) return;
    try {
        await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skill/reset`, {
            method: 'POST', json: { rel: SK_CURRENT, content: '' },
        });
        showToast('已重置为代码默认', 'success');
        const list = await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skills`);
        SK_FILES = list.skills || [];
        _skRenderList();
        await skLoadCurrent();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// ===== 轻量 Markdown 渲染（预览用） =====
function _skInline(s) {
    return _skEsc(s)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function skRenderMd(md) {
    let lines = String(md || '').split(/\r?\n/);
    // 去掉 YAML frontmatter（--- 包裹的头部元数据），预览不展示
    if (lines.length && lines[0].trim() === '---') {
        for (let i = 1; i < lines.length; i++) {
            if (lines[i].trim() === '---') { lines = lines.slice(i + 1); break; }
        }
    }
    const out = [];
    let code = null, para = [];

    const flushPara = () => {
        if (para.length) { out.push(`<p>${_skInline(para.join(' '))}</p>`); para = []; }
    };
    // 列表/引用/表格用缓冲数组
    let listBuf = null, listTag = null, quoteBuf = null, tableBuf = null;
    const flushList2 = () => {
        if (listBuf) { out.push(`<${listTag}>${listBuf.join('')}</${listTag}>`); listBuf = null; listTag = null; }
    };
    const flushQuote = () => {
        if (quoteBuf) { out.push(`<blockquote>${quoteBuf.map(_skInline).join('<br>')}</blockquote>`); quoteBuf = null; }
    };
    const flushTable = () => {
        if (tableBuf && tableBuf.length) {
            const rows = tableBuf.filter(r => !/^\s*\|?[\s:|-]+\|?\s*$/.test(r));
            const head = rows.shift();
            if (!head) { tableBuf = null; return; }
            const cells = r => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
            let html = '<table><thead><tr>' + cells(head).map(c => `<th>${_skInline(c)}</th>`).join('') + '</tr></thead><tbody>';
            html += rows.map(r => '<tr>' + cells(r).map(c => `<td>${_skInline(c)}</td>`).join('') + '</tr>').join('');
            out.push(html + '</tbody></table>');
        }
        tableBuf = null;
    };
    const flushAll = () => { flushPara(); flushList2(); flushQuote(); flushTable(); };

    for (const raw of lines) {
        if (code !== null) {
            if (/^```/.test(raw.trim())) { out.push(`<pre><code>${_skEsc(code.join('\n'))}</code></pre>`); code = null; }
            else code.push(raw);
            continue;
        }
        const t = raw.trim();
        if (/^```/.test(t)) { flushAll(); code = []; continue; }
        if (!t) { flushAll(); continue; }
        const h = t.match(/^(#{1,4})\s+(.*)$/);
        if (h) { flushAll(); out.push(`<h${h[1].length}>${_skInline(h[2])}</h${h[1].length}>`); continue; }
        if (/^(-{3,}|\*{3,})$/.test(t)) { flushAll(); out.push('<hr>'); continue; }
        if (t.startsWith('>')) { flushPara(); flushList2(); flushTable(); (quoteBuf = quoteBuf || []).push(t.replace(/^>\s?/, '')); continue; }
        if (t.startsWith('|')) { flushPara(); flushList2(); flushQuote(); (tableBuf = tableBuf || []).push(t); continue; }
        const ul = t.match(/^[-*]\s+(.*)$/);
        if (ul) {
            flushPara(); flushQuote(); flushTable();
            if (listTag !== 'ul') { flushList2(); listTag = 'ul'; listBuf = []; }
            listBuf.push(`<li>${_skInline(ul[1])}</li>`); continue;
        }
        const ol = t.match(/^(\d+)[\.、]\s+(.*)$/);
        if (ol) {
            flushPara(); flushQuote(); flushTable();
            if (listTag !== 'ol') { flushList2(); listTag = 'ol'; listBuf = []; }
            // Keep the number written in Markdown. DOCX imports intentionally
            // separate paragraphs with blank lines, so the browser may receive
            // several short <ol> blocks and would otherwise restart each at 1.
            listBuf.push(`<li value="${ol[1]}">${_skInline(ol[2])}</li>`); continue;
        }
        flushList2(); flushQuote(); flushTable();
        para.push(t);
    }
    if (code !== null) out.push(`<pre><code>${_skEsc(code.join('\n'))}</code></pre>`);
    flushAll();
    return out.join('\n');
}
