/**
 * Know-how 页：业务查看/编辑各小节 Know-how 原文的独立侧边栏页面。
 * 原文可以在本页保存并编译为提取规则、生成 SKILL、审核 SKILL 三件套；三件套应用后
 * 可继续在 Skill 管理页分别编辑，点击提取/生成/审核时读取对应生效版本。
 * 依赖 app.js 的 showToast/navigate/_escHtmlAttr；api.js 的 API；skills_admin.js 的 skRenderMd。
 */

let KH_PACK_ID = null;
let KH_SECTIONS = [];   // 全部官方小节（含未配置）
let KH_CURRENT = null;  // 当前选中小节 id，如 "2.3"
let KH_CONTENT = null;  // {content, default_content, overridden}
let KH_MODE = 'preview';
let KH_DIRTY = false;
let KH_COMPILE = null;  // 最近一次编译预览

async function knowhowPageEnter() {
    if (!currentProjectId) {
        try {
            const ps = await API.getProjects();
            const list = Array.isArray(ps) ? ps : (ps.projects || []);
            if (list.length) currentProjectId = list[0].id;
        } catch (_) { /* 下面统一提示 */ }
    }
    const box = document.getElementById('khList');
    if (!currentProjectId) {
        if (box) box.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0">请先在项目列表中创建或选择项目</div>';
        return;
    }
    try {
        const packs = await API.get('/packs');
        KH_PACK_ID = packs.default_id || ((packs.packs || [])[0] || {}).id;
        const resp = await API.listAllSkillSections();
        KH_SECTIONS = resp.sections || [];
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    if (!KH_CURRENT || !KH_SECTIONS.find(s => s.id === KH_CURRENT)) {
        KH_CURRENT = KH_SECTIONS.length ? KH_SECTIONS[0].id : null;
    }
    _khRenderList();
    await khLoadCurrent();
}

function _khRenderList() {
    const box = document.getElementById('khList');
    if (!box) return;
    const groups = {};
    KH_SECTIONS.forEach(s => {
        groups[s.chapter_n] ||= { title: s.chapter_title, items: [] };
        groups[s.chapter_n].items.push(s);
    });
    box.innerHTML = Object.keys(groups).sort((a, b) => Number(a) - Number(b)).map(n => {
        const g = groups[n];
        const item = s => `
            <div class="sk-item ${s.id === KH_CURRENT ? 'active' : ''}" onclick="khSelect('${s.id}')">
                <span class="sk-item-label">${_escHtmlAttr(s.id)} ${_escHtmlAttr(s.title)}</span>
                ${!s.configured ? '<span class="badge badge-extra" title="尚无生产管线，可先写 Know-how 草稿">未配置</span>' : ''}
            </div>`;
        return `<div class="sk-group-title">第${n}章 · ${_escHtmlAttr(g.title)}</div>${g.items.map(item).join('')}`;
    }).join('');
}

async function khSelect(sectionId) {
    if (KH_DIRTY && !confirm('当前修改尚未保存，切换将丢失，确定切换？')) return;
    KH_CURRENT = sectionId;
    KH_DIRTY = false;
    KH_COMPILE = null;
    _khRenderList();
    await khLoadCurrent();
}

function _khRelFor(sectionId) {
    const s = KH_SECTIONS.find(x => x.id === sectionId);
    const dir = (s && s.skill) || `section-skills/reits-section-${sectionId.replace('.', '-')}`;
    return `${dir}/SKILL.md`;
}

async function khLoadCurrent() {
    const box = document.getElementById('khMain');
    if (!box || !KH_CURRENT) return;
    box.innerHTML = '<div class="card"><div class="card-body text-muted">加载中…</div></div>';
    try {
        KH_CONTENT = await API.get(`/packs/${encodeURIComponent(KH_PACK_ID)}/skill`, { rel: _khRelFor(KH_CURRENT) });
    } catch (e) {
        showToast(e.message, 'error');
        return;
    }
    KH_DIRTY = false;
    _khRenderMain();
}

function khDirty() { KH_DIRTY = true; }

function khSetMode(mode) {
    if (mode === KH_MODE) return;
    KH_MODE = mode;
    _khRenderMain();
}

function _khRenderMain() {
    const box = document.getElementById('khMain');
    const d = KH_CONTENT;
    const s = KH_SECTIONS.find(x => x.id === KH_CURRENT);
    if (!box || !d || !s) return;
    const statusBadge = d.overridden
        ? '<span class="badge badge-warning">已修改</span>'
        : (s.configured ? '<span class="badge badge-info">代码默认</span>' : '<span class="badge badge-extra">未配置</span>');
    box.innerHTML = `
    <div class="card">
        <div class="card-header sk-head">
            <h3>${_escHtmlAttr(s.id)} ${_escHtmlAttr(s.title)} ${statusBadge}</h3>
            <div class="flex gap-8">
                <div class="sk-seg">
                    <div class="sk-seg-item ${KH_MODE === 'preview' ? 'active' : ''}" onclick="khSetMode('preview')">预览</div>
                    <div class="sk-seg-item ${KH_MODE === 'edit' ? 'active' : ''}" onclick="khSetMode('edit')">编辑</div>
                </div>
                ${s.configured ? `<button class="btn btn-ghost btn-sm" id="khCompileBtn" onclick="khCompile()">${KH_MODE === 'edit' ? '保存并编译' : 'AI 编译三件套'}</button>` : ''}
                <button class="btn btn-primary btn-sm" id="khSaveBtn" onclick="khSave()" style="${KH_MODE === 'edit' ? '' : 'display:none'}">仅保存原文</button>
            </div>
        </div>
        <div class="card-body">
            ${KH_MODE === 'edit' ? `
                <div class="text-muted text-sm" style="margin-bottom:8px">
                    “仅保存原文”不会启动提取；“保存并编译”会生成三件套预览，确认应用后才更新执行规则。
                </div>
                <textarea id="khEditor" class="sk-editor" oninput="khDirty()" spellcheck="false">${_escHtmlAttr(d.content)}</textarea>
            ` : `
                <div class="sk-preview md-body">${skRenderMd(d.content)}</div>
            `}
        </div>
    </div>${_khRenderCompilePreview()}`;
}

function _khRenderCompilePreview() {
    const r = KH_COMPILE;
    if (!r) return '';
    if (!r.ok) return `<div class="card" style="margin-top:12px"><div class="card-header"><h4>编译未通过</h4></div><div class="card-body"><ul>${(r.errors || []).map(e => `<li>${_escHtmlAttr(e)}</li>`).join('')}</ul>${r.raw ? `<pre class="sk-editor">${_escHtmlAttr(r.raw)}</pre>` : ''}</div></div>`;
    const m = r.merge_report || {};
    const artifacts = r.artifacts || {};
    return `<div class="card" style="margin-top:12px">
        <div class="card-header sk-head"><h4>三件套编译预览（尚未生效）</h4><div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="KH_COMPILE=null;_khRenderMain()">丢弃</button><button class="btn btn-primary btn-sm" onclick="khApplyCompile()">应用三件套</button></div></div>
        <div class="card-body">
            <div class="foundation-flow"><span>新增 <b>${m.added || 0}</b></span><i>→</i><span>复用/合并 <b>${m.reused || 0}</b></span><i>→</i><span>冲突提示 <b>${(m.conflicts || []).length}</b></span></div>
            ${(m.merged || []).length ? `<details><summary>查看字段合并关系</summary><pre>${_escHtmlAttr(JSON.stringify(m.merged, null, 2))}</pre></details>` : ''}
            ${(m.conflicts || []).length ? `<div class="foundation-alert warn">存在规则差异，系统按本次编译规则执行并保留共享关系：<pre>${_escHtmlAttr(JSON.stringify(m.conflicts, null, 2))}</pre></div>` : ''}
            <details open><summary>提取规则 EXTRACTION_RULES.json</summary><pre class="sk-editor" style="max-height:340px">${_escHtmlAttr(artifacts.extraction || '')}</pre></details>
            <details><summary>生成 SKILL</summary><pre class="sk-editor" style="max-height:340px">${_escHtmlAttr(artifacts.generation || '')}</pre></details>
            <details><summary>AI 审核 SKILL</summary><pre class="sk-editor" style="max-height:340px">${_escHtmlAttr(artifacts.audit || '')}</pre></details>
        </div>
    </div>`;
}

async function khCompile() {
    const s = KH_SECTIONS.find(x => x.id === KH_CURRENT);
    if (!s || !s.configured) { showToast('该小节尚未配置生产入口', 'warning'); return; }
    const ta = document.getElementById('khEditor');
    const text = ta ? ta.value : (KH_CONTENT?.content || '');
    if (!String(text).trim()) { showToast('Know-how 原文为空，无法编译', 'warning'); return; }
    const btn = document.getElementById('khCompileBtn');
    if (btn) { btn.disabled = true; btn.textContent = '正在编译…'; }
    try {
        if (ta && KH_DIRTY) {
            await API.post(`/packs/${encodeURIComponent(KH_PACK_ID)}/skill/save`,
                { rel: _khRelFor(KH_CURRENT), content: text });
            KH_CONTENT.content = text; KH_CONTENT.overridden = true; KH_DIRTY = false;
        }
        KH_COMPILE = await API.recompileSection(KH_CURRENT, text);
        _khRenderMain();
        showToast(KH_COMPILE.ok ? '三件套预览已生成，请核对后应用' : '编译结果未通过结构校验', KH_COMPILE.ok ? 'success' : 'warning');
    } catch (e) {
        showToast('编译失败：' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '保存并编译'; }
    }
}

async function khApplyCompile() {
    if (!KH_COMPILE?.ok || !KH_COMPILE.preview) return;
    if (!confirm(`应用小节 ${KH_CURRENT} 的提取规则、生成 SKILL 和审核 SKILL？应用后不会自动提取数据。`)) return;
    try {
        await API.applyRecompiledSection(KH_CURRENT, KH_COMPILE.preview);
        KH_COMPILE = null;
        _khRenderMain();
        showToast('三件套已应用；可在 Skill 管理分别编辑，点击“提取数据”后按新规则执行', 'success');
    } catch (e) { showToast('应用失败：' + e.message, 'error'); }
}

async function khSave() {
    const ta = document.getElementById('khEditor');
    if (!ta) return;
    const btn = document.getElementById('khSaveBtn');
    btn.disabled = true; btn.textContent = '保存中…';
    try {
        await API.post(`/packs/${encodeURIComponent(KH_PACK_ID)}/skill/save`,
            { rel: _khRelFor(KH_CURRENT), content: ta.value });
        showToast('已保存 Know-how 原文；未改变当前执行规则', 'success');
        KH_DIRTY = false;
        const resp = await API.listAllSkillSections();
        KH_SECTIONS = resp.sections || [];
        _khRenderList();
        await khLoadCurrent();
    } catch (e) {
        showToast(e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '仅保存原文'; }
    }
}

/** 从其它页面（如"未配置小节"提示）跳到 Know-how 页并定位到指定小节。 */
function goToKnowhow(sectionId) {
    if (sectionId) KH_CURRENT = sectionId;
    navigate('knowhow');
}
