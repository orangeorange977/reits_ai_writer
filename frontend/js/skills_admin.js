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
    if (!SK_CURRENT || !SK_FILES.find(f => f.rel === SK_CURRENT)) {
        SK_CURRENT = SK_FILES.length ? SK_FILES[0].rel : null;
    }
    _skRenderList();
    await skLoadCurrent();
}

function _skRenderList() {
    const box = document.getElementById('skList');
    const chapters = SK_FILES.filter(f => f.kind === 'chapter');
    const globals = SK_FILES.filter(f => f.kind === 'global');
    const item = f => `
        <div class="sk-item ${f.rel === SK_CURRENT ? 'active' : ''}" onclick="skSelect('${_skEsc(f.rel)}')">
            <span class="sk-item-label">${_skEsc(f.label)}</span>
            ${f.overridden ? '<span class="sk-dot" title="已被用户修改"></span>' : ''}
        </div>`;
    box.innerHTML = `
        <div class="sk-group-title">各章写作要求</div>
        ${chapters.map(item).join('')}
        <div class="sk-group-title">全局</div>
        ${globals.map(item).join('')}`;
}

async function skSelect(rel) {
    if (SK_DIRTY && !confirm('当前修改尚未保存，切换将丢失，确定切换？')) return;
    SK_CURRENT = rel;
    SK_DIRTY = false;
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
                <button class="btn btn-primary btn-sm" id="skSaveBtn" onclick="skSave()" style="${SK_MODE === 'edit' ? '' : 'display:none'}">保存修改</button>
            </div>
        </div>
        <div class="card-body">
            ${SK_MODE === 'edit' ? `
                <div class="text-muted text-sm" style="margin-bottom:8px">
                    修改保存后，后续各章 AI 生成即时按新版本执行；代码内置版本不受影响，可随时"重置为默认"。
                </div>
                <textarea id="skEditor" class="sk-editor" oninput="skDirty()" spellcheck="false">${_skEsc(d.content)}</textarea>
            ` : `
                <div class="sk-preview md-body">${skRenderMd(d.content)}</div>
            `}
        </div>
    </div>`;
}

function skDirty() { SK_DIRTY = true; }

async function skSave() {
    const ta = document.getElementById('skEditor');
    if (!ta) return;
    const btn = document.getElementById('skSaveBtn');
    btn.disabled = true; btn.textContent = '保存中…';
    try {
        await _skJson(`/packs/${encodeURIComponent(SK_PACK_ID)}/skill/save`, {
            method: 'POST', json: { rel: SK_CURRENT, content: ta.value },
        });
        showToast('已保存，后续生成即时生效', 'success');
        SK_DIRTY = false;
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
        const ol = t.match(/^\d+[\.、]\s+(.*)$/);
        if (ol) {
            flushPara(); flushQuote(); flushTable();
            if (listTag !== 'ol') { flushList2(); listTag = 'ol'; listBuf = []; }
            listBuf.push(`<li>${_skInline(ol[1])}</li>`); continue;
        }
        flushList2(); flushQuote(); flushTable();
        para.push(t);
    }
    if (code !== null) out.push(`<pre><code>${_skEsc(code.join('\n'))}</code></pre>`);
    flushAll();
    return out.join('\n');
}
