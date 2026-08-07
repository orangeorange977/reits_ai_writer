/**
 * REIT-AI 法律文件生成系统 - 主应用逻辑
 */

// 页面标题映射
const PAGE_TITLES = {
    'overview': '系统概览',
    'ndrc': '材料生成',
    'documents': '文档管理',
    'settings': '系统设置'
};

// 当前状态
let currentPage = 'overview';
let currentProjectId = null;
let currentChapter = 'chapter1';
// 章节结构改由项目绑定的模板包提供（PACK_CHAPTERS），此处不再缓存旧管线章节数据
// 最近一次从后端拉到的项目列表，供项目信息栏等展示真实数据用
let _projectsCache = [];

// 当前项目绑定的模板包（来自 /api/packs/{id}）：章节结构随包走，不再写死七章
let PACK_INFO = null;      // manifest：{id, name, version, material_label, ...}
let PACK_CHAPTERS = [];    // [{n, title}, ...]（按 n 升序）

/** 第 n 章标题：优先用绑定包的 chapters.json，包未加载时落回内置兼容表 */
function _chapterTitle(n) {
    const c = (PACK_CHAPTERS || []).find(ch => ch.n === n);
    return (c && c.title) || CHAPTER_TITLES[n] || '';
}

/**
 * 页面导航切换
 * @param {string} pageId - 目标页面ID
 */
function navigate(pageId) {
    // 隐藏所有页面
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });

    // 显示目标页面
    const targetPage = document.getElementById(`page-${pageId}`);
    if (targetPage) {
        targetPage.classList.add('active');
    }

    // 进设置页/申报材料页时刷新当前项目的材料列表（步骤 3.4）
    if (pageId === 'settings' || pageId === 'ndrc') {
        loadMaterialsUI();
    }

    // 更新侧边栏激活状态
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    const activeNav = document.querySelector(`.nav-item[data-page="${pageId}"]`);
    if (activeNav) {
        activeNav.classList.add('active');
    }

    // 更新页面标题（材料生成页优先用项目绑定模板包的名称）
    const titleEl = document.getElementById('pageTitle');
    if (titleEl) {
        let t = PAGE_TITLES[pageId];
        if (pageId === 'ndrc' && PACK_INFO && PACK_INFO.name) t = PACK_INFO.name;
        if (t) titleEl.textContent = t;
    }

    currentPage = pageId;

    // 页面切换后加载对应数据
    onPageEnter(pageId);
}

/**
 * 页面进入时加载数据
 */
async function onPageEnter(pageId) {
    switch (pageId) {
        case 'overview':
            await loadOverviewData();
            break;
        case 'ndrc':
            if (currentProjectId) {
                await loadChapters();
            }
            break;
        case 'documents':
            if (currentProjectId) {
                await loadDocuments();
            }
            break;
        case 'settings':
            await loadModelSetting();
            break;
    }
}

/** 加载 AI 模型下拉：列出 DeepSeek/Kimi 两厂商可用模型（分组），选中当前使用的 */
async function loadModelSetting() {
    const sel = document.getElementById('settingModel');
    if (!sel) return;
    try {
        const resp = await API.getModels();
        const models = resp.models || [];
        const current = resp.current || '';
        if (!models.length) {
            sel.innerHTML = `<option value="${_escHtmlAttr(current)}">${_escHtmlAttr(current || '（无法获取模型列表）')}</option>`;
            return;
        }
        // 按厂商分组：deepseek 前缀 → DeepSeek，其余 → Kimi
        const deepseek = models.filter(m => m.toLowerCase().startsWith('deepseek'));
        const kimi = models.filter(m => !m.toLowerCase().startsWith('deepseek'));
        const optHtml = m =>
            `<option value="${_escHtmlAttr(m)}"${m === current ? ' selected' : ''}>${_escHtmlAttr(m)}</option>`;
        let html = '';
        if (deepseek.length) html += `<optgroup label="DeepSeek">${deepseek.map(optHtml).join('')}</optgroup>`;
        if (kimi.length) html += `<optgroup label="Kimi（Moonshot）">${kimi.map(optHtml).join('')}</optgroup>`;
        sel.innerHTML = html;
    } catch (e) {
        sel.innerHTML = `<option value="">获取失败：${_escHtmlAttr(e.message)}</option>`;
    }
}

/** 保存所选 AI 模型（即时生效，各章生成都用它） */
async function saveModelSetting(model) {
    if (!model) return;
    try {
        await API.setModel(model);
        showToast('已切换模型：' + model);
    } catch (e) {
        showToast('切换模型失败：' + e.message, 'error');
    }
}

// ===== 申报材料管理（步骤 3.4：上传模式，按当前项目隔离）=====

/** 文件大小友好显示 */
function _fmtSize(bytes) {
    if (bytes >= 1024 * 1024 * 1024) return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB';
    if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return bytes + ' B';
}

/** 后端存的是 UTC 时间字符串（SQLite CURRENT_TIMESTAMP），展示时统一换算为北京时间 */
function _fmtTime(t) {
    if (!t || t === '-') return '-';
    const d = new Date(String(t).trim().replace(' ', 'T') + 'Z');
    if (isNaN(d.getTime())) return t;
    return d.toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }).slice(0, 16);
}

// ===== 溯源跳转：点“📎 依据”/参考材料清单，直接定位到对应出处 =====

/** 材料文件清单缓存（按项目缓存；上传/清空后失效） */
let _matFileCache = null; // {pid, files}
async function _getMaterialFiles() {
    const pid = currentProjectId;
    if (!_matFileCache || _matFileCache.pid !== pid) {
        const d = await API.listMaterials();
        _matFileCache = { pid, files: ((d && d.files) || []).map(f => f.path).filter(Boolean) };
    }
    return _matFileCache.files;
}
function _invalidateMatCache() { _matFileCache = null; }

/** 按文件名（可无目录）模糊匹配材料库中的真实路径 */
async function _findMaterialPath(nameRaw) {
    let name = String(nameRaw || '').trim().replace(/^📄\s*/, '').replace(/[《》]/g, '').trim();
    if (name.includes('/')) name = name.split('/').pop();
    if (!name) return null;
    const files = await _getMaterialFiles();
    const base = p => p.split('/').pop();
    let hit = files.find(p => base(p) === name);
    if (!hit) hit = files.find(p => base(p).includes(name) || name.includes(base(p)));
    if (!hit) {
        const stem = name.replace(/\.[^.]+$/, '');
        if (stem.length >= 4) {
            hit = files.find(p => {
                const s2 = base(p).replace(/\.[^.]+$/, '');
                return s2.includes(stem) || stem.includes(s2);
            });
        }
    }
    return hit || null;
}

/** 依据里的路径可能只有文件名：先模糊匹配出真实路径再预览，匹配不到再按原路径试 */
async function openMaterialPreviewResolved(path, quote) {
    if (!path.includes('/')) {
        const real = await _findMaterialPath(path);
        if (real) { openMaterialPreview(real, quote); return; }
    }
    openMaterialPreview(path, quote);
}

/** 点“参考材料”清单项（只有文件名）：按名定位后打开原文预览 */
async function openRefByName(text) {
    try {
        const path = await _findMaterialPath(text);
        if (path) { openMaterialPreview(path, ''); return; }
    } catch (e) { /* 材料列表拉取失败则提示 */ }
    showToast('未在当前项目材料库中找到该文件（可能已被删除或未上传）', 'warning');
}

/** 解析一条依据标注并跳转：申报材料→预览原文并高亮摘录；摘要表→定位字段行 */
function openSrcLink(rawText) {
    const text = String(rawText || '').replace(/^📎\s*依据[：:]/, '').trim();
    if (!text) return;
    for (const seg of text.split(/[；;]/).map(s => s.trim()).filter(Boolean)) {
        let m;
        if ((m = seg.match(/^申报材料[：:](.+)$/))) {
            const body = m[1].trim();
            const qm = body.match(/〈([^〉]*)〉/);
            let path = body.replace(/〈[^〉]*〉/, '').trim();
            // 兼容文件名被《》包裹的写法：《xx.xlsx》→ xx.xlsx
            path = path.replace(/^《(.+)》$/, '$1').trim();
            if (path) { openMaterialPreviewResolved(path, qm ? qm[1].trim() : ''); return; }
        }
        if ((m = seg.match(/^摘要表[：:](.+)$/))) { jumpToSummaryField(m[1].trim()); return; }
    }
    // 无类型前缀的裸文件名（部分旧格式）：尝试按名定位
    if (text.length <= 60 && !/^(天眼查|网络公开|固定表述|planning)/.test(text)) {
        openRefByName(text); return;
    }
    showToast('这类依据无法定位原文（如天眼查实时查询、网络信息、固定表述等）', 'warning');
}

/** 材料原文预览弹窗：加载解析文本，高亮“依据”里摘录的原句并滚动到该处 */
async function openMaterialPreview(path, quote) {
    let modal = document.getElementById('matPreviewModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'matPreviewModal';
        modal.className = 'mat-preview-overlay';
        modal.innerHTML = `
            <div class="mat-preview-box">
                <div class="mat-preview-head">
                    <span class="mat-preview-title" id="matPreviewTitle">正在加载材料…</span>
                    <button class="btn btn-ghost btn-sm" onclick="closeMaterialPreview()">✕ 关闭</button>
                </div>
                <div class="mat-preview-body" id="matPreviewBody"></div>
            </div>`;
        modal.addEventListener('click', (e) => { if (e.target === modal) closeMaterialPreview(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.style.display === 'flex') closeMaterialPreview();
        });
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
    document.getElementById('matPreviewTitle').textContent = '正在加载材料原文…';
    document.getElementById('matPreviewBody').innerHTML =
        '<div class="text-muted" style="padding:20px">正在解析材料，请稍候…（扫描件需要识别文字，会稍慢）</div>';
    try {
        const d = await API.previewMaterial(path);
        document.getElementById('matPreviewTitle').textContent = `📄 《${d.filename}》原文`;
        const body = document.getElementById('matPreviewBody');
        const text = d.text || '（未能解析出该文件的文字）';
        // 先逐字匹配摘录；不行再用摘录开头段兜底（标点空格可能有微小出入）
        let idx = quote ? text.indexOf(quote) : -1;
        let markLen = quote ? quote.length : 0;
        if (idx < 0 && quote) {
            const head = quote.slice(0, Math.max(6, Math.min(14, quote.length)));
            idx = text.indexOf(head);
            markLen = head.length;
        }
        let tip = '';
        if (quote && idx >= 0) {
            tip = '<div class="src-tip ok">✅ 已按“依据”摘录定位到原文（下方高亮处），可结合上下文核对。</div>';
        } else if (quote) {
            tip = `<div class="src-tip warn">⚠️ 摘录未能在本文档中逐字定位（可能略有出入），摘录内容：“${_escHtmlAttr(quote)}”，请人工核对。</div>`;
        }
        if (idx >= 0) {
            body.innerHTML = tip
                + `<div class="mat-text">${_escHtmlAttr(text.slice(0, idx))}`
                + `<mark class="src-mark">${_escHtmlAttr(text.slice(idx, idx + markLen))}</mark>`
                + `${_escHtmlAttr(text.slice(idx + markLen))}</div>`;
            const mk = body.querySelector('.src-mark');
            if (mk) setTimeout(() => mk.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60);
        } else {
            body.innerHTML = tip + `<div class="mat-text">${_escHtmlAttr(text)}</div>`;
        }
    } catch (e) {
        document.getElementById('matPreviewTitle').textContent = '材料加载失败';
        document.getElementById('matPreviewBody').innerHTML =
            `<div style="padding:20px;color:var(--danger)">${_escHtmlAttr(e.message)}</div>`;
    }
}

function closeMaterialPreview() {
    const modal = document.getElementById('matPreviewModal');
    if (modal) modal.style.display = 'none';
}

/** “摘要表：字段”→跳到摘要表编辑页，高亮定位到对应字段行 */
async function jumpToSummaryField(field) {
    await selectSummary();
    await new Promise(r => setTimeout(r, 500));
    const rows = document.querySelectorAll('#chapterDetail .kv-row');
    let hit = null;
    for (const row of rows) {
        const key = row.querySelector('.kv-key');
        if (key && key.value && (key.value.includes(field) || field.includes(key.value))) { hit = row; break; }
    }
    if (!hit) { showToast(`摘要表里没找到“${field}”字段，可能在释义/其他基本信息里，或名称略有出入`, 'warning'); return; }
    hit.scrollIntoView({ behavior: 'smooth', block: 'center' });
    hit.classList.add('field-highlight');
    setTimeout(() => hit.classList.remove('field-highlight'), 4000);
}

// 事件委托：点“📎 依据”行/引注项/参考材料清单 → 跳转出处
document.addEventListener('click', (e) => {
    const item = e.target.closest && e.target.closest('.src-item');
    if (item) { openSrcLink(item.textContent); return; }   // 逐句引注：点哪条跳哪条
    const srcEl = e.target.closest && e.target.closest('.doc-src');
    if (srcEl) { openSrcLink(srcEl.textContent); return; }
    const refEl = e.target.closest && e.target.closest('.ref-item');
    if (refEl) { openRefByName(refEl.textContent); return; }
});

/** 申报材料面板展开/收起（自包含切换，不走增强面板的 tab 逻辑，避免重置其他面板状态） */
function toggleMaterialsPanel(headerEl) {
    const body = document.getElementById('materials-body');
    if (!body) return;
    const icon = headerEl ? headerEl.querySelector('.toggle-icon') : null;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    if (icon) icon.textContent = open ? '▼' : '▲';
    if (!open) loadMaterialsUI();
}

/** 把材料路径列表按目录层级建树：{dirs: Map(名称->节点), files:[{name,size}]}；dirs 参数补全空目录 */
function _buildMaterialsTree(files, dirs) {
    const root = { dirs: new Map(), files: [] };
    const ensure = (segs) => {
        let node = root;
        for (const seg of segs) {
            if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), files: [] });
            node = node.dirs.get(seg);
        }
        return node;
    };
    for (const d of (dirs || [])) ensure(String(d || '').split('/').filter(Boolean));
    for (const f of files) {
        const segs = String(f.path || '').split('/').filter(Boolean);
        const fname = segs.pop();
        ensure(segs).files.push({ name: fname, size: f.size });
    }
    return root;
}

/** 递归统计节点下文件总数（分栏里文件夹后面显示数量） */
function _countTreeFiles(node) {
    let n = node.files.length;
    for (const sub of node.dirs.values()) n += _countTreeFiles(sub);
    return n;
}

/** 仿 Finder 分栏浏览：点文件夹右侧展开下一栏，点文件选中（可横向滚动） */
function renderColumnBrowser(container, files, dirs) {
    const root = _buildMaterialsTree(files, dirs);
    container.innerHTML = '';
    container.classList.add('mc-browser');
    const cols = [];

    const makeCol = (node, level) => {
        const col = document.createElement('div');
        col.className = 'mc-col';
        let h = '';
        const dirNames = Array.from(node.dirs.keys()).sort((a, b) => a.localeCompare(b, 'zh'));
        for (const d of dirNames) {
            h += `<div class="mc-row mc-dir" data-name="${_escHtmlAttr(d)}" data-type="dir">
                <span class="mc-ico">📁</span><span class="mc-name" title="${_escHtmlAttr(d)}">${_escHtmlAttr(d)}</span>
                <span class="mc-meta">${_countTreeFiles(node.dirs.get(d))}</span><span class="mc-arrow">▸</span></div>`;
        }
        for (const f of node.files.sort((a, b) => a.name.localeCompare(b.name, 'zh'))) {
            h += `<div class="mc-row mc-file" data-name="${_escHtmlAttr(f.name)}" data-type="file" title="${_escHtmlAttr(f.name)}">
                <span class="mc-ico">📄</span><span class="mc-name">${_escHtmlAttr(f.name)}</span>
                <span class="mc-meta">${_fmtSize(f.size)}</span></div>`;
        }
        col.innerHTML = h || '<div class="mc-empty">（空文件夹）</div>';
        col.addEventListener('click', (e) => {
            const row = e.target.closest('.mc-row');
            if (!row) return;
            // 移除更深的栏（重新选择后右侧作废）
            cols.slice(level + 1).forEach(c => c.remove());
            cols.length = level + 1;
            col.querySelectorAll('.mc-row.selected').forEach(r => r.classList.remove('selected'));
            row.classList.add('selected');
            if (row.dataset.type === 'dir') {
                const nc = makeCol(node.dirs.get(row.dataset.name), level + 1);
                cols.push(nc);
                container.appendChild(nc);
                container.scrollLeft = container.scrollWidth;
            }
        });
        return col;
    };

    const first = makeCol(root, 0);
    cols.push(first);
    container.appendChild(first);
}

/** 拉取当前项目的材料列表并渲染（设置页平铺 + 申报材料页树形面板） */
async function loadMaterialsUI() {
    try {
        const data = await API.listMaterials();
        const statText = data.total_files > 0
            ? `已上传 ${data.total_files} 个文件，共 ${_fmtSize(data.total_size)}`
            : '尚未上传材料';
        // 设置页（仿 Finder 分栏浏览）
        const stat = document.getElementById('materialsStat');
        const list = document.getElementById('materialsFileList');
        if (stat) stat.textContent = statText;
        if (list) {
            if (data.total_files === 0) {
                list.classList.remove('mc-browser');
                list.innerHTML = '<div class="text-muted text-sm">暂无材料</div>';
            } else {
                renderColumnBrowser(list, data.files, data.dirs);
            }
        }
        // 申报材料页面板（树形列表，保留文件夹层级）
        const pCount = document.getElementById('matPanelCount');
        const pStat = document.getElementById('matPanelStat');
        const pList = document.getElementById('matPanelList');
        if (pCount) pCount.textContent = data.total_files > 0 ? `（${data.total_files} 个文件）` : '';
        if (pStat) pStat.textContent = statText;
        if (pList) {
            if (data.total_files === 0) {
                pList.classList.remove('mc-browser');
                pList.innerHTML = '<div class="text-muted text-sm">暂无材料，请上传项目相关的申报材料（支持整个文件夹）</div>';
            } else {
                renderColumnBrowser(pList, data.files, data.dirs);
            }
        }
    } catch (e) {
        const stat = document.getElementById('materialsStat');
        if (stat) stat.textContent = '材料列表加载失败';
        const pStat = document.getElementById('matPanelStat');
        if (pStat) pStat.textContent = '材料列表加载失败';
    }
}

/** 选择文件后上传（支持多选，zip 后端自动解压） */
async function onUploadMaterials(input) {
    const files = input.files;
    if (!files || files.length === 0) return;
    const stat = document.getElementById('materialsStat');
    const pStat = document.getElementById('matPanelStat');
    const oldText = stat ? stat.textContent : '';
    try {
        const tip = `正在上传 ${files.length} 个文件…（大文件/解压需要一点时间）`;
        if (stat) stat.textContent = tip;
        if (pStat) pStat.textContent = tip;
        const result = await API.uploadMaterials(files);
        _invalidateMatCache();
        const parts = [];
        if (result.uploaded && result.uploaded.length) parts.push(`直传 ${result.uploaded.length} 个`);
        if (result.extracted_from_zip) parts.push(`zip 解压出 ${result.extracted_from_zip} 个`);
        if (result.skipped && result.skipped.length) parts.push(`跳过不支持的格式 ${result.skipped.length} 个`);
        showToast('上传完成：' + (parts.join('，') || '无新增文件'));
        await loadMaterialsUI();
    } catch (e) {
        showToast('上传失败：' + e.message, 'error');
        if (stat) stat.textContent = oldText;
        if (pStat) pStat.textContent = oldText;
    } finally {
        input.value = '';  // 允许重复选择同一个文件
    }
}

/** 清空当前项目的全部材料（双重确认：先确认意图，再输入“清空”防误触） */
async function clearMaterialsUI() {
    if (!confirm('您即将清空当前项目的全部申报材料，此操作不可恢复！\n\n确定要继续吗？')) return;
    const v = prompt('为防止误操作，请输入“清空”两个字确认：');
    if (v === null) return;
    if (v.trim() !== '清空') { showToast('已取消清空（未输入确认词）', 'warning'); return; }
    try {
        await API.clearMaterials();
        _invalidateMatCache();
        showToast('材料已清空');
        await loadMaterialsUI();
    } catch (e) {
        showToast('清空失败：' + e.message, 'error');
    }
}

/**
 * 显示Toast提示消息
 * @param {string} message - 提示消息
 * @param {string} type - 类型：'success' | 'error' | 'warning'
 */
function showToast(message, type = 'success') {
    const toast = document.getElementById('globalToast');
    if (!toast) return;

    toast.textContent = message;
    toast.className = 'toast show';
    if (type === 'error') toast.classList.add('error');
    if (type === 'warning') toast.classList.add('warning');

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

/**
 * 打开弹窗
 * @param {string} id - 弹窗ID
 */
function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.add('show');
    }
}

/**
 * 关闭弹窗
 * @param {string} id - 弹窗ID
 */
function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) {
        modal.classList.remove('show');
    }
}

// ===== 概览页面功能 =====

/**
 * 加载概览页面数据
 */
async function loadOverviewData() {
    try {
        const projects = await API.getProjects();
        _projectsCache = projects || [];
        const tbody = document.querySelector('#projectTable tbody');
        if (tbody) {
            if (projects.length > 0) {
                renderProjectTable(tbody, projects.map(p => ({
                    name: p.name,
                    assetType: '数据中心',
                    stage: '发改委申报',
                    status: _mapProjectStatus(p.status),
                    updateTime: _fmtTime(p.updated_at || p.created_at),
                    id: p.id,
                    isDemo: !!p.is_demo,
                })));
                // 自动选择第一个项目
                if (!currentProjectId && projects.length > 0) {
                    currentProjectId = projects[0].id;
                }
            } else {
                tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;padding:24px">暂无项目</td></tr>';
            }
        }
        updateProjectHeaderBar();

        // 更新统计卡片
        const statsContainer = document.getElementById('overviewStats');
        if (statsContainer) {
            const generating = projects.filter(p => p.status === 'generating').length;
            const completed = projects.filter(p => p.status === 'generated').length;
            renderStatCards(statsContainer, [
                { icon: '📁', value: projects.length, label: '项目总数', color: 'blue' },
                { icon: '🔄', value: generating, label: '生成中', color: 'orange' },
                { icon: '✅', value: completed, label: '已完成', color: 'green' },
                { icon: '📐', value: 1, label: '模板数', color: 'purple' },
            ]);
        }
    } catch (error) {
        console.warn('[REIT-AI] 加载项目数据失败:', error.message);
    }
}

/**
 * 映射项目状态为中文显示
 */
function _mapProjectStatus(status) {
    const map = {
        'active': '待处理',
        'scanned': '已扫描',
        'generating': '生成中',
        'generated': '已完成',
        'generation_failed': '错误',
    };
    return map[status] || status;
}

/**
 * 按当前项目渲染“发改委材料生成”页顶部的项目信息栏（真实数据）
 */
function updateProjectHeaderBar() {
    const titleEl = document.getElementById('projectHeaderTitle');
    const metaEl = document.getElementById('projectHeaderMeta');
    if (!titleEl || !metaEl) return;
    const proj = _projectsCache.find(p => p.id === currentProjectId);
    if (!proj) {
        titleEl.textContent = '未选择项目';
        metaEl.textContent = '请先在系统概览中选择或创建项目';
        return;
    }
    titleEl.textContent = proj.name || '未命名项目';
    metaEl.textContent = `状态：${_mapProjectStatus(proj.status)} | 更新时间：${_fmtTime(proj.updated_at || proj.created_at)}`;
}

// ===== 发改委材料生成页面功能 =====

// ===== 数据源文件夹选择器（步骤 3.3：只允许浏览 DATA_SOURCE_BASE 内的目录，任意路径浏览已删除） =====

let _picker = { targetInputId: '' };  // 当前选择器要把路径填回哪个输入框

/**
 * 打开数据源文件夹选择器（新建项目弹窗用）：只能浏览数据源根目录内的文件夹。
 * @param {string} targetInputId - 选中的路径要填回哪个输入框的id
 */
async function openSourcePicker(targetInputId) {
    _picker = { targetInputId };
    const input = document.getElementById(targetInputId);
    const startPath = input ? input.value.trim() : '';

    // 优先从输入框旧值打开；旧值不在白名单内/不存在时回退到数据源根目录
    const candidates = startPath ? [startPath, ''] : [''];
    for (const candidate of candidates) {
        try {
            const result = await API.browseFolder(candidate);
            showFolderBrowser(result);
            return;
        } catch (error) {
            // 这个候选路径打不开，继续尝试下一个兜底路径
        }
    }
    showToast('浏览失败，无法定位起始路径', 'error');
}

/**
 * 显示文件夹浏览弹窗：文件夹可点进去，文件可直接点选
 */
function showFolderBrowser(data) {
    const modal = document.getElementById('modal-folder-browser');
    if (!modal) return;

    const pathDisplay = modal.querySelector('#folderBrowserPath');
    const listContainer = modal.querySelector('#folderBrowserList');

    if (pathDisplay) {
        pathDisplay.textContent = data.current_path || '-';
    }

    if (listContainer) {
        let html = '';

        // 上级目录按钮（到磁盘根目录时parent_path为空字符串，点击后回到磁盘列表）
        if (data.parent_path || data.current_path) {
            html += `<div class="folder-item" onclick="navigateFolder('${(data.parent_path || '').replace(/\\/g, '\\\\')}')">
                <span class="folder-icon">⬆️</span>
                <span class="folder-name">..</span>
            </div>`;
        }

        // 目录列表（文件只展示不可选：数据源选择器只需要选文件夹）
        for (const item of data.items) {
            if (item.type === 'dir') {
                html += `<div class="folder-item" onclick="navigateFolder('${item.path.replace(/\\/g, '\\\\')}')">
                    <span class="folder-icon">📁</span>
                    <span class="folder-name">${item.name}</span>
                </div>`;
            } else {
                html += `<div class="folder-item" style="cursor:default;opacity:.6">
                    <span class="folder-icon">📄</span>
                    <span class="folder-name">${item.name}</span>
                    <span class="folder-size">${item.size_formatted || ''}</span>
                </div>`;
            }
        }

        listContainer.innerHTML = html;
    }

    openModal('modal-folder-browser');
}

/**
 * 在文件夹浏览器中导航
 */
async function navigateFolder(path) {
    try {
        const result = await API.browseFolder(path);
        showFolderBrowser(result);
    } catch (error) {
        showToast('无法访问该目录: ' + error.message, 'error');
    }
}

/**
 * 选择当前浏览的文件夹，填回对应输入框（弹窗底部"选择此文件夹"按钮）
 */
function selectCurrentFolder() {
    const pathDisplay = document.getElementById('folderBrowserPath');
    const input = document.getElementById(_picker.targetInputId);
    if (pathDisplay && input) {
        input.value = pathDisplay.textContent;
    }
    closeModal('modal-folder-browser');
    showToast('已选择文件夹');
}

/**
 * 加载章节列表：先拉当前项目绑定模板包的章节结构，再渲染步骤条与第一章编辑区
 */
async function loadChapters() {
    if (!currentProjectId) return;

    try {
        await loadProjectPack();
        renderChapterStepper();
        // 默认展示第一章（走 Kimi 生成 + Word 式编辑视图）
        await renderChapterEditor(1);
        // 底部整体进度条：按各章是否有内容计算（新管线）
        await updateChapterProgress();
    } catch (error) {
        console.warn('[REIT-AI] 加载章节列表失败:', error.message);
    }
}

/**
 * 拉取当前项目绑定模板包的 manifest + 章节结构；同步刷新依赖包名的界面文案
 */
async function loadProjectPack() {
    PACK_INFO = null;
    PACK_CHAPTERS = [];
    if (!currentProjectId) return;
    try {
        const proj = _projectsCache.find(p => p.id === currentProjectId);
        let packId = proj && proj.pack_id ? proj.pack_id : null;
        if (!packId) {
            const packs = await API.getPacks();
            packId = packs.default_id;
        }
        if (packId) {
            const detail = await API.getPackDetail(packId);
            PACK_INFO = detail.pack || null;
            PACK_CHAPTERS = detail.chapters || [];
        }
    } catch (error) {
        console.warn('[REIT-AI] 加载模板包信息失败:', error.message);
    }
    _applyPackLabels();
}

/** 按绑定包刷新界面文案：侧边栏固定为"申报材料"（避免长包名显示不全），页面标题用包名 */
function _applyPackLabels() {
    const label = (PACK_INFO && PACK_INFO.name) ? PACK_INFO.name : '申报材料';
    const navLabel = document.getElementById('navMaterialLabel');
    if (navLabel) {
        navLabel.textContent = '申报材料';
    }
    if (currentPage === 'ndrc') {
        const titleEl = document.getElementById('pageTitle');
        if (titleEl) titleEl.textContent = label;
    }
}

/**
 * 渲染章节步骤条（标题来自项目绑定模板包的 chapters.json，不再写死七章）
 */
function renderChapterStepper() {
    const container = document.getElementById('chapterStepper');
    if (!container) return;

    // 第一项固定为"摘要表和释义"（不属于章节，走独立的点击逻辑）；
    // 圆圈不指定 circle，由 renderStepper 统一按序号显示（1，章节依次 2~8）
    const summaryStep = {
        title: '摘要表和释义',
        status: '',
        desc: '项目基础信息',
        onClick: 'selectSummary()',
    };

    const chapterSteps = (PACK_CHAPTERS || []).map(ch => ({
        title: ch.title,
        status: '',
        desc: '待生成',
        onClick: `selectChapter(${ch.n})`,
    }));

    renderStepper(container, [summaryStep, ...chapterSteps]);

    // 异步核对各章实际生成状态，更新步骤条徽标（总复查修复：列表与编辑器状态不一致）
    (PACK_CHAPTERS || []).forEach((ch, i) => {
        API.getChapterContent(ch.n).then(content => {
            if (content && content.source === 'ready') {
                _markStepperDone(ch.n);
            }
        }).catch(() => { /* 单章查不到不影响其他章 */ });
        // 刷新页面/重进后恢复“生成中”状态：橙色脉冲 + 全局横幅 + 继续轮询
        API.getChapterStatus(ch.n).then(st => {
            if (st && st.status === 'running' && !_kimiTimer) {
                _pollChapterGeneration(ch.n);
            }
        }).catch(() => {});
    });
}

/** 立即把第 n 章在步骤条上标记为“已生成”（✓ + 文案），
 * 避免生成完成后要切页重进步骤条才更新的“延迟”问题。 */
function _markStepperDone(n) {
    const container = document.getElementById('chapterStepper');
    if (!container) return;
    const step = _stepperStepFor(container, n);
    if (!step) return;
    step.classList.remove('current');
    step.classList.add('done');
    const circle = step.querySelector('.step-circle');
    if (circle) circle.textContent = '✓';
    const desc = step.querySelector('.step-desc');
    if (desc) desc.textContent = '已生成';
}

/** 把第 n 章在步骤条上标记为“生成中”（橙色圆圈 + 脉冲动画），一眼可见当前在生成哪章 */
function _markStepperRunning(n) {
    const container = document.getElementById('chapterStepper');
    if (!container) return;
    const step = _stepperStepFor(container, n);
    if (!step) return;
    step.classList.remove('done');
    step.classList.add('current');
    const circle = step.querySelector('.step-circle');
    if (circle) circle.textContent = '⏳';
    const desc = step.querySelector('.step-desc');
    if (desc) desc.textContent = '生成中…';
}

/** 生成失败/中断时把第 n 章步骤条恢复为待生成 */
function _markStepperIdle(n) {
    const container = document.getElementById('chapterStepper');
    if (!container) return;
    const step = _stepperStepFor(container, n);
    if (!step) return;
    step.classList.remove('current', 'done');
    const idx = (PACK_CHAPTERS || []).findIndex(ch => ch.n === n);
    const circle = step.querySelector('.step-circle');
    if (circle && idx >= 0) circle.textContent = idx + 2;  // 首项是摘要表，章节从 2 开始
    const desc = step.querySelector('.step-desc');
    if (desc) desc.textContent = '待生成';
}

function _stepperStepFor(container, n) {
    const idx = (PACK_CHAPTERS || []).findIndex(ch => ch.n === n);
    if (idx < 0) return null;
    return container.querySelectorAll('.step')[idx + 1];  // +1：首项是摘要表
}

// ===== 全局生成中横幅：固定悬浮在页面顶部，切到任何章节/页面都能看到当前在生成哪章 =====
function _showGlobalGenBanner(n) {
    let banner = document.getElementById('globalGenBanner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'globalGenBanner';
        document.body.appendChild(banner);
    }
    const title = _chapterTitle(n) || `第 ${n} 章`;
    banner.innerHTML = `
        <span class="ggb-dot"></span>
        <span>🤖 AI 正在生成：<b>${_escHtmlAttr(title)}</b>&nbsp;约需数分钟，期间可继续做别的</span>
        <button class="ggb-go" onclick="_goToChapter(${n})">去看看 →</button>`;
    banner.style.display = 'flex';
    banner.dataset.chapter = n;
}

function _hideGlobalGenBanner() {
    const banner = document.getElementById('globalGenBanner');
    if (banner) banner.style.display = 'none';
}

/** 横幅“去看看”：先切到申报材料页，再打开正在生成的章（修 bug：之前只渲染编辑区不切页） */
function _goToChapter(n) {
    navigate('ndrc');
    selectChapter(n);
}

/**
 * 选择章节：全部走 Kimi 生成 + Word 式编辑视图（章节结构随项目绑定的模板包）
 * @param {number} chapterNum - 章节编号（由包 chapters.json 定义）
 */
async function selectChapter(chapterNum) {
    await renderChapterEditor(chapterNum);
}

let _summaryData = null;

// 三个区块对应 _summaryData 里的键，用于 data-group 定位
const _SUMMARY_GROUPS = [
    { key: 'summary_table', title: '摘要表' },
    { key: 'glossary', title: '释义' },
    { key: 'other_info', title: '其他基本信息' },
];

/**
 * 选择"摘要表和释义"：在章节详情区渲染可展开的编辑区（摘要表 / 释义 / 其他基本信息）
 */
async function selectSummary() {
    currentChapter = 'summary';
    const container = document.getElementById('chapterDetail');
    if (!container) return;

    // 先渲染骨架（含加载中 + 顶部导入按钮）
    container.innerHTML = `
        <div class="chapter-detail-header">
            <div class="flex items-center gap-12">
                <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">摘要表和释义</h3>
            </div>
            <div class="flex gap-8">
                <button class="btn btn-ghost btn-sm" onclick="document.getElementById('summaryExcelInput').click()">📥 上传Excel导入</button>
                <input type="file" id="summaryExcelInput" accept=".xlsx,.xls" style="display:none" onchange="importSummaryExcel(this)">
                <button class="btn btn-primary btn-sm" onclick="saveSummary()">💾 保存</button>
            </div>
        </div>
        <div class="chapter-detail-body">
            <div class="sections-tree" id="summarySections">
                <div class="text-sm text-muted" style="padding:8px 0;">加载中…</div>
            </div>
        </div>
    `;

    try {
        const resp = await API.getSummary();
        _summaryData = resp.data || { summary_table: [], glossary: [], other_info: [] };
    } catch (e) {
        showToast('加载摘要表/释义失败: ' + e.message, 'error');
        return;
    }

    renderSummarySections();
}

/**
 * 根据 _summaryData 渲染三个可展开区块（键、值均可编辑，可增删行）
 */
function renderSummarySections() {
    const wrap = document.getElementById('summarySections');
    if (!wrap) return;

    wrap.innerHTML = _SUMMARY_GROUPS.map(g => `
        <div class="section-item">
            <div class="section-header" onclick="toggleSection(this)">
                <span class="toggle-icon">▶</span>
                <span class="section-title">${g.title}</span>
                <span class="field-status">(${(_summaryData[g.key] || []).length}条)</span>
            </div>
            <div class="section-body" style="display:none;">
                ${_renderEditableKV(g.key, _summaryData[g.key] || [])}
            </div>
        </div>
    `).join('');
}

// 每个区块的撤销栈：结构性操作（插入/删除/拖动）和每次聚焦后的首次编辑前，都会压入快照
const _summaryUndo = { summary_table: [], glossary: [], other_info: [] };
// 拖动上下文
let _summaryDrag = null;

/** 压入一份当前数据的快照，供撤销 */
function _pushUndo(groupKey) {
    if (!_summaryUndo[groupKey]) _summaryUndo[groupKey] = [];
    _summaryUndo[groupKey].push(JSON.parse(JSON.stringify(_summaryData[groupKey] || [])));
    if (_summaryUndo[groupKey].length > 50) _summaryUndo[groupKey].shift();
}

/**
 * 渲染"键+值"可编辑行：拖动句柄 + 键 + 值 + [插入/撤销/删除] 按钮
 */
function _renderEditableKV(groupKey, rows) {
    const esc = (s) => (s == null ? '' : String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));
    let html = `<div class="kv-editor" data-group="${groupKey}">`;
    rows.forEach((r, i) => {
        // 释义的第一行（简称 / 释义）作为不可编辑的列标题保留（不可拖动/删除/插入）
        if (groupKey === 'glossary' && i === 0) {
            html += `<div class="kv-row kv-title-row">
                <span></span>
                <span class="kv-title">${esc(r.label)}</span>
                <span class="kv-title">${esc(r.value)}</span>
                <span></span>
            </div>`;
            return;
        }
        html += `<div class="kv-row" data-idx="${i}" draggable="false"
            ondragstart="summaryDragStart(event,'${groupKey}',${i})"
            ondragover="summaryDragOver(event,this)"
            ondragleave="this.classList.remove('drag-over')"
            ondrop="summaryDrop(event,'${groupKey}',${i})"
            ondragend="summaryDragEnd(this)">
            <span class="kv-handle" title="按住此处拖动调整位置"
                onmousedown="this.parentElement.setAttribute('draggable','true')"
                onmouseup="this.parentElement.setAttribute('draggable','false')">⠿</span>
            <input type="text" class="form-input kv-key" value="${esc(r.label)}"
                onfocus="this._snap=false" oninput="onSummaryEdit('${groupKey}', ${i}, 'label', this.value, this)">
            <input type="text" class="form-input kv-val" value="${esc(r.value)}"
                onfocus="this._snap=false" oninput="onSummaryEdit('${groupKey}', ${i}, 'value', this.value, this)">
            <span class="kv-actions">
                <button class="btn btn-ghost btn-sm kv-ins" title="在此行上方插入一行" onclick="insertSummaryRow('${groupKey}', ${i})">＋</button>
                <button class="btn btn-ghost btn-sm kv-undo" title="撤销上一步操作" onclick="undoSummary('${groupKey}')">↶</button>
                <button class="btn btn-ghost btn-sm kv-del" title="删除此行" onclick="deleteSummaryRow('${groupKey}', ${i})">✕</button>
            </span>
        </div>`;
    });
    html += `<button class="btn btn-ghost btn-sm" style="margin-top:8px" onclick="addSummaryRow('${groupKey}')">＋ 在末尾新增一行</button>`;
    html += `</div>`;
    return html;
}

/** 用户编辑键/值时同步到内存数据；聚焦后首次输入前先存一份快照供撤销 */
function onSummaryEdit(groupKey, idx, field, value, el) {
    if (!_summaryData || !_summaryData[groupKey] || !_summaryData[groupKey][idx]) return;
    if (el && !el._snap) { _pushUndo(groupKey); el._snap = true; }
    _summaryData[groupKey][idx][field === 'label' ? 'label' : 'value'] = value;
}

/** 在末尾新增一行 */
function addSummaryRow(groupKey) {
    _pushUndo(groupKey);
    if (!_summaryData[groupKey]) _summaryData[groupKey] = [];
    _summaryData[groupKey].push({ label: '', value: '' });
    _rerenderSummaryGroup(groupKey);
}

/** 在指定行的上方插入一行 */
function insertSummaryRow(groupKey, idx) {
    _pushUndo(groupKey);
    if (!_summaryData[groupKey]) _summaryData[groupKey] = [];
    _summaryData[groupKey].splice(idx, 0, { label: '', value: '' });
    _rerenderSummaryGroup(groupKey);
}

/** 删除一行 */
function deleteSummaryRow(groupKey, idx) {
    if (!_summaryData[groupKey]) return;
    _pushUndo(groupKey);
    _summaryData[groupKey].splice(idx, 1);
    _rerenderSummaryGroup(groupKey);
}

/** 撤销上一步操作（结构性操作或一次编辑） */
function undoSummary(groupKey) {
    const stack = _summaryUndo[groupKey] || [];
    if (!stack.length) { showToast('没有可撤销的操作', 'warning'); return; }
    _summaryData[groupKey] = stack.pop();
    _rerenderSummaryGroup(groupKey);
    showToast('已撤销');
}

// ===== 拖动排序 =====
function summaryDragStart(ev, groupKey, idx) {
    _summaryDrag = { groupKey, idx };
    ev.dataTransfer.effectAllowed = 'move';
}
function summaryDragOver(ev, rowEl) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    rowEl.classList.add('drag-over');
}
function summaryDrop(ev, groupKey, toIdx) {
    ev.preventDefault();
    document.querySelectorAll('.kv-row.drag-over').forEach(el => el.classList.remove('drag-over'));
    if (!_summaryDrag || _summaryDrag.groupKey !== groupKey) return;
    const from = _summaryDrag.idx;
    _summaryDrag = null;
    if (from === toIdx) return;
    _pushUndo(groupKey);
    const arr = _summaryData[groupKey];
    const [moved] = arr.splice(from, 1);
    arr.splice(toIdx, 0, moved);
    _rerenderSummaryGroup(groupKey);
}
function summaryDragEnd(rowEl) {
    document.querySelectorAll('.kv-row.drag-over').forEach(el => el.classList.remove('drag-over'));
    // 拖完把行恢复成不可拖动，这样在输入框里选文字复制不会误触发拖动
    if (rowEl && rowEl.setAttribute) rowEl.setAttribute('draggable', 'false');
    document.querySelectorAll('.kv-row[draggable="true"]').forEach(el => el.setAttribute('draggable', 'false'));
    _summaryDrag = null;
}

/** 只重渲染某个区块的 body，并保持它展开 */
function _rerenderSummaryGroup(groupKey) {
    const editor = document.querySelector(`.kv-editor[data-group="${groupKey}"]`);
    if (!editor) return;
    const body = editor.closest('.section-body');
    const header = body.previousElementSibling;
    body.innerHTML = _renderEditableKV(groupKey, _summaryData[groupKey] || []);
    // 更新条数
    const statusEl = header.querySelector('.field-status');
    if (statusEl) statusEl.textContent = `(${(_summaryData[groupKey] || []).length}条)`;
    // 保持展开
    body.style.display = 'block';
    const icon = header.querySelector('.toggle-icon');
    if (icon) icon.textContent = '▼';
}

/**
 * 保存摘要表/释义/其他基本信息到后端（之后各章生成都以此为准）
 */
async function saveSummary() {
    if (!_summaryData) return;
    try {
        await API.saveSummary({
            summary_table: _summaryData.summary_table || [],
            glossary: _summaryData.glossary || [],
            other_info: _summaryData.other_info || [],
        });
        showToast('已保存，后续章节生成将以此为准');
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

/**
 * 上传 Excel 导入（三个 sheet：摘要表/释义/其他基本信息）
 */
async function importSummaryExcel(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
        const resp = await API.importSummaryExcel(file);
        _summaryData = resp.data || { summary_table: [], glossary: [], other_info: [] };
        renderSummarySections();
        showToast('Excel 导入成功');
    } catch (e) {
        showToast('导入失败: ' + e.message, 'error');
    } finally {
        input.value = ''; // 允许再次选择同一文件
    }
}

// ===== 章节：Kimi 生成 + Word 式可编辑区（章节结构随包） =====

let _kimiTimer = null;
let _previewOn = false;      // Word 预览默认关闭，由按钮开启
let _editorChapter = 1;      // 当前编辑区所属章节号
let _previewCache = {};      // 各章已生成的预览HTML缓存（内容没变时开关预览直接用，不重复写入）

// 章节标题（与官方模板 Heading1 一字不差）
const CHAPTER_TITLES = {
    1: '一、项目基本情况',
    2: '二、参与主体情况',
    3: '三、REITs设立方案',
    4: '四、项目基本条件',
    5: '五、项目合规情况',
    6: '六、运营管理安排',
    7: '七、募集资金用途情况',
};

const _escHtmlAttr = (s) => (s == null ? '' : String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));

/** 开启/关闭右侧 Word 预览（切换显隐，不重渲染编辑区，避免丢失未保存编辑） */
function toggleChapterPreview() {
    _previewOn = !_previewOn;
    const col = document.getElementById('ch1PreviewCol');
    const btn = document.getElementById('btnChapterPreviewToggle');
    if (col) col.style.display = _previewOn ? '' : 'none';
    if (btn) btn.textContent = _previewOn ? '📄 关闭Word预览' : '📄 开启Word预览';
    if (_previewOn) refreshChapterPreview();
}

/**
 * 渲染第 n 章编辑视图：各子标题下是 Word 式整块可编辑区（内容来自 Kimi 的可读文本）
 * 点击步骤条对应章节时调用
 */
async function renderChapterEditor(n) {
    _editorChapter = n;
    currentChapter = 'chapter' + n;
    const container = document.getElementById('chapterDetail');
    if (!container) return;

    // 小标题骨架由后端自动回退到材料包内置官方模板；即使还没生成也能看到本章小标题
    let content = { source: 'none', sections: [] };
    try {
        content = await API.getChapterContent(n);
    } catch (e) { /* 后端未就绪，按空处理 */ }
    // 本章已生成→同步刷新步骤条状态（兜底：覆盖跨设备/后台已生成的情况）
    if (content.source === 'ready') _markStepperDone(n);
    // 本章正在生成→显示生成中状态并接入轮询（覆盖“去看看”跳进来/切章进来的场景）
    API.getChapterStatus(n).then(st => {
        if (st && st.status === 'running') {
            _markStepperRunning(n);
            _showGlobalGenBanner(n);
            const btn2 = document.getElementById('btnChapterGen');
            const banner2 = document.getElementById('chapterGenBanner');
            if (btn2) { btn2.disabled = true; btn2.textContent = '⏳ 生成中...'; }
            if (banner2) { banner2.style.display = 'block'; banner2.textContent = `🤖 AI 正在生成${_chapterTitle(n) || '本章'}，约需数分钟，请稍候…`; }
            if (!_kimiTimer) _pollChapterGeneration(n);
        }
    }).catch(() => {});

    const hasSections = content.sections && content.sections.length > 0;
    const srcBadge = content.source === 'ready'
        ? '<span class="badge badge-success">已生成</span>'
        : (content.source === 'template' ? '<span class="badge">未生成</span>' : '');
    const title = _chapterTitle(n);

    // 顶部标题栏（整行）
    let html = `
        <div class="chapter-detail-header">
            <div class="flex items-center gap-12">
                <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">${title}</h3>
                ${srcBadge}
            </div>
            <div class="flex gap-8">
                <button class="btn btn-ghost btn-sm" id="btnChapterGen" onclick="runKimiChapter()">🤖 ${content.source === 'ready' ? '重新生成' : 'AI 生成'}</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertFootnote()" title="把光标放到正文中要加脚注的位置，再点此">➕ 脚注</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertDiagram()" title="把光标放到正文中要插图的位置，再点此画框图">🖼 画图</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="openAIAssist()" title="先在正文里选中一段文字，再点此让AI润色/改写/扩写等">✨ AI辅助</button>
                <button class="btn btn-ghost btn-sm" id="btnChapterPreviewToggle" onclick="toggleChapterPreview()">📄 ${_previewOn ? '关闭Word预览' : '开启Word预览'}</button>
                <button class="btn btn-primary btn-sm" onclick="saveChapter()">💾 保存</button>
            </div>
        </div>
        <div id="chapterGenBanner" class="kimi-status" style="display:none;margin-bottom:12px"></div>`;

    // 本章参考材料（业务化清单：只列 AI 写作时参考了哪些材料，不展示技术细节）
    if (content.refs && content.refs.length) {
        const items = content.refs.map(r => `<div class="ref-item">📄 ${_escHtmlAttr(r)}</div>`).join('');
        html += `<details class="refs-panel">
            <summary>📋 本章生成参考了以下材料（共 ${content.refs.length} 项，正文每段下方另有“依据”标注） · 点击展开</summary>
            <div class="refs-list">${items}</div>
        </details>`;
    }

    // 左右分栏：左=编辑区，右=Word 预览（默认隐藏，由开启按钮控制）
    html += `<div class="ch1-split">
        <div class="ch1-editor-col">
            <div class="sections-tree">`;

    if (!hasSections) {
        html += `<div class="text-sm text-muted" style="padding:8px 0;">未读到本章小标题。请先到"系统设置"里选择官方模板文件路径——本章的小标题会按模板自动列在下面，每个小标题一个编辑区。</div>`;
    } else {
        if (content.source === 'template') {
            html += `<div class="text-sm text-muted" style="padding:4px 0 8px;">以下小标题来自官方模板。点右上角"AI 生成"自动填写，或直接在各小标题下编辑。</div>`;
        }
        content.sections.forEach(sec => {
            html += `
                <div class="section-item">
                    <div class="section-header" onclick="toggleSection(this)">
                        <span class="toggle-icon">▶</span>
                        <span class="section-title">${_escHtmlAttr(sec.title)}</span>
                    </div>
                    <div class="section-body" style="display:none;">
                        <div class="doc-editor" contenteditable="true" data-secid="${_escHtmlAttr(sec.id)}" data-title="${_escHtmlAttr(sec.title)}">${sec.html || '<p></p>'}</div>
                    </div>
                </div>`;
        });
    }

    html += `</div>
            <div id="ch1FootnotePanel" class="fn-panel" style="display:none;"></div>
        </div>
        <div class="ch1-preview-col" id="ch1PreviewCol" style="${_previewOn ? '' : 'display:none'}">
            <div class="ch1-preview-head">
                <span>📄 Word 实时预览</span>
                <span class="flex gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="API.downloadChapterDocx(${n})">⬇ 下载Word</button>
                </span>
            </div>
            <div id="ch1PreviewBody" class="ch1-preview-body">
                <div class="text-muted text-sm" style="padding:8px 0;">保存后可在此预览 Word 输出效果。</div>
            </div>
        </div>
    </div>`;

    container.innerHTML = html;

    _ensureFootnoteHandlers();
    _ensureTableToolbar();
    _renumberFootnotes();
    _renderDiagramPlaceholders();

    // 预览开启且已有生成内容时，自动生成一次预览
    if (_previewOn && content.source === 'ready') refreshChapterPreview();

    // 若后台正有一个生成任务在跑，接管轮询
    try {
        const st = await API.getChapterStatus(n);
        if (st.status === 'running') _pollChapterGeneration();
    } catch (e) { /* ignore */ }
}

/** 生成/刷新当前章 Word 预览（写入系统设置里的官方模板对应章节） */
async function refreshChapterPreview(force = false) {
    const body = document.getElementById('ch1PreviewBody');
    if (!body) return;
    const n = _editorChapter;
    // 内容没变（未保存/未重新生成）时，开关预览直接复用缓存，不再重复写入模板，秒开
    if (!force && _previewCache[n] != null) {
        body.innerHTML = _previewCache[n];
        return;
    }
    body.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0;">正 在写入模板并生成预览…</div>';
    // 模板文件由后端自动回退到材料包内置 template.docx（步骤 3.4）
    try {
        const resp = await API.getChapterPreview(n);
        if (resp.has_content) {
            body.innerHTML = resp.html;
            _previewCache[n] = resp.html;  // 缓存，供下次开关预览直接用
            if (!resp.used_template) {
                showToast('未使用官方模板，已用独立文档预览（材料包内置模板未找到）', 'warning');
            }
        } else {
            body.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0;">暂无内容，请先生成并保存本章。</div>';
        }
    } catch (e) {
        body.innerHTML = `<div class="kimi-status error">生成预览失败：${e.message}</div>`;
    }
}

/**
 * 点击"用Kimi生成"：启动当前章生成（异步），轮询，完成后重渲染编辑视图
 */
async function runKimiChapter() {
    // 材料目录由后端自动解析到当前项目的上传目录（步骤 3.4：无需再传本机路径）
    try {
        await API.runChapter(_editorChapter);
    } catch (error) {
        if (!String(error.message).includes('正在生成')) {
            showToast('启动失败: ' + error.message, 'error');
            return;
        }
    }
    _pollChapterGeneration();
}

/** 轮询生成进度；完成后重渲染当前章编辑视图。
 * n 缺省用当前编辑章；支持在其它章节上为正在生成的章轮询（全局横幅场景）。 */
function _pollChapterGeneration(n) {
    n = n || _editorChapter;
    const btn = document.getElementById('btnChapterGen');
    const banner = document.getElementById('chapterGenBanner');
    // 步骤条橙色脉冲 + 全局悬浮横幅：无论用户在哪个章节/页面，都能一眼看到正在生成哪章
    _markStepperRunning(n);
    _showGlobalGenBanner(n);
    if (_editorChapter === n) {
        if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中...'; }
        if (banner) { banner.style.display = 'block'; banner.textContent = `🤖 AI 正在生成${_chapterTitle(n) || '本章'}，约需数分钟，请稍候…`; }
    }

    if (_kimiTimer) clearInterval(_kimiTimer);
    _kimiTimer = setInterval(async () => {
        let st;
        try { st = await API.getChapterStatus(n); } catch (e) { return; }
        if (st.status === 'done') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            delete _previewCache[n];   // 重新生成了，预览缓存作废
            _hideGlobalGenBanner();
            _markStepperDone(n);       // 步骤条立即变“✓ 已生成”，不再等切页
            if (_editorChapter === n) {
                await renderChapterEditor(n);   // 只有正看着这章才重渲染，不打断用户看别的章
            }
            showToast(`生成完成：${_chapterTitle(n) || '本章'}，请核对编辑`);
        } else if (st.status === 'error') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            _hideGlobalGenBanner();
            _markStepperIdle(n);
            if (_editorChapter === n) {
                const btn2 = document.getElementById('btnChapterGen');
                const banner2 = document.getElementById('chapterGenBanner');
                if (btn2) { btn2.disabled = false; btn2.textContent = '🤖 AI 生成'; }
                if (banner2) { banner2.className = 'kimi-status error'; banner2.textContent = '生成失败：' + (st.error || '未知错误'); }
            }
            showToast(`生成失败：${_chapterTitle(n) || '本章'}`, 'error');
        }
    }, 3000);
}

/**
 * 保存当前章：收集每个子标题的 Word 编辑区内容，回传给 reading skill（持久化）
 */
async function saveChapter() {
    const editors = document.querySelectorAll('#chapterDetail .doc-editor');
    if (!editors.length) { showToast('没有可保存的内容，请先生成', 'warning'); return; }
    const sections = Array.from(editors).map(ed => ({
        id: ed.dataset.secid || '',
        title: ed.dataset.title || '',
        html: ed.innerHTML,
    }));
    try {
        await API.saveChapterContent(_editorChapter, sections);
        delete _previewCache[_editorChapter];   // 内容已改，缓存作废
        showToast('已保存，并返回给 reading skill');
        // 预览开启时，保存后自动让 writing skill 写入 Word 并刷新预览（强制重生成）
        if (_previewOn) refreshChapterPreview(true);
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

// ===== 脚注 =====
let _lastEditorRange = null;      // 最近一次落在编辑区里的光标/选区
let _footnoteHandlersReady = false;

/** 一次性注册：追踪编辑区光标位置 + 双击脚注编辑/删除 */
function _ensureFootnoteHandlers() {
    if (_footnoteHandlersReady) return;
    _footnoteHandlersReady = true;

    document.addEventListener('selectionchange', () => {
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        const r = sel.getRangeAt(0);
        let node = r.startContainer;
        if (node && node.nodeType === 3) node = node.parentElement;
        if (node && node.closest && node.closest('.doc-editor')) {
            _lastEditorRange = r.cloneRange();
        }
    });

    // 点图块上的删除按钮：整块删除（连同紧随的空段落），删完记得保存
    document.addEventListener('click', (e) => {
        const del = e.target && e.target.closest && e.target.closest('.doc-diagram-del');
        if (!del) return;
        e.preventDefault();
        e.stopPropagation();
        const block = del.closest('.doc-diagram');
        if (!block) return;
        const next = block.nextElementSibling;
        block.remove();
        if (next && next.tagName === 'P' && !next.textContent.trim()
            && !next.querySelector('img, sup, .doc-diagram')) {
            next.remove();
        }
        showToast('已删除图，记得点"保存"');
    });

    // 双击已有脚注：编辑内容，清空则删除
    document.addEventListener('dblclick', (e) => {
        const dia = e.target && e.target.closest && e.target.closest('.doc-diagram');
        if (dia) { _editDiagramBlock(dia); return; }
        const sup = e.target && e.target.closest && e.target.closest('sup.fn-ref');
        if (!sup) return;
        const cur = sup.dataset.fn || '';
        const nv = prompt('编辑脚注内容（清空后确定则删除该脚注）：', cur);
        if (nv === null) return;
        if (!nv.trim()) sup.remove();
        else sup.dataset.fn = nv.trim();
        _renumberFootnotes();
    });

    // 在编辑区直接删掉脚注上标数字时，同步重排编号并刷新下方注释面板
    // （只在脚注数量变化时刷新，避免影响正常打字/中文输入）
    document.addEventListener('input', (e) => {
        if (!(e.target && e.target.closest && e.target.closest('.doc-editor'))) return;
        const n = document.querySelectorAll('#chapterDetail .doc-editor sup.fn-ref').length;
        if (n !== _lastFootnoteCount) {
            _lastFootnoteCount = n;
            _renumberFootnotes();
        }
    });
}

let _lastFootnoteCount = -1;

/* ============ 编辑区表格操作（增删行/列、合并/拆分单元格） ============ */
let _tblToolbarReady = false;
let _tblCell = null;   // 当前光标所在的 td/th

function _ensureTableToolbar() {
    if (_tblToolbarReady) return;
    _tblToolbarReady = true;

    const bar = document.createElement('div');
    bar.className = 'tbl-toolbar';
    bar.style.display = 'none';
    const btns = [
        ['↑ 插行', '在上方插入一行', () => _tblInsertRow(false)],
        ['↓ 插行', '在下方插入一行', () => _tblInsertRow(true)],
        ['✖ 删行', '删除本行', () => _tblDeleteRow()],
        ['← 插列', '在左侧插入一列', () => _tblInsertCol(false)],
        ['→ 插列', '在右侧插入一列', () => _tblInsertCol(true)],
        ['✖ 删列', '删除本列', () => _tblDeleteCol()],
        ['⇥ 合并右', '与右邻单元格合并', () => _tblMergeRight()],
        ['⤓ 合并下', '与下方单元格合并', () => _tblMergeDown()],
        ['⤢ 拆分', '取消本单元格的合并', () => _tblSplit()],
    ];
    btns.forEach(([label, title, fn]) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'tbl-tb-btn';
        b.textContent = label;
        b.title = title;
        // mousedown 阻止默认，避免点按钮时丢失编辑区选区
        b.addEventListener('mousedown', (e) => { e.preventDefault(); });
        b.addEventListener('click', (e) => {
            e.preventDefault();
            if (!_tblCell || !_tblCell.isConnected) return;
            fn();
            _positionTableToolbar();
        });
        bar.appendChild(b);
    });
    document.body.appendChild(bar);
    _tblToolbar = bar;

    document.addEventListener('selectionchange', () => {
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        let node = sel.getRangeAt(0).startContainer;
        if (node && node.nodeType === 3) node = node.parentElement;
        const cell = node && node.closest ? node.closest('.doc-editor td, .doc-editor th') : null;
        if (cell) { _tblCell = cell; _positionTableToolbar(); }
        else { _tblCell = null; bar.style.display = 'none'; }
    });
    window.addEventListener('scroll', () => { if (_tblCell) _positionTableToolbar(); }, true);
}
let _tblToolbar = null;

function _positionTableToolbar() {
    if (!_tblToolbar || !_tblCell || !_tblCell.isConnected) {
        if (_tblToolbar) _tblToolbar.style.display = 'none';
        return;
    }
    const table = _tblCell.closest('table');
    if (!table) { _tblToolbar.style.display = 'none'; return; }
    const rect = table.getBoundingClientRect();
    _tblToolbar.style.display = 'flex';
    let top = window.scrollY + rect.top - _tblToolbar.offsetHeight - 6;
    if (top < window.scrollY + 4) top = window.scrollY + rect.bottom + 6; // 顶部放不下就放表格下方
    _tblToolbar.style.top = top + 'px';
    _tblToolbar.style.left = (window.scrollX + rect.left) + 'px';
}

/** 构建表格的逻辑网格：grid[r][c] = {cell, anchor:bool}，正确处理 colspan/rowspan。 */
function _tblBuildGrid(table) {
    const rows = Array.from(table.rows);
    const grid = [];
    rows.forEach((tr, r) => {
        if (!grid[r]) grid[r] = [];
        let c = 0;
        Array.from(tr.cells).forEach(cell => {
            while (grid[r][c]) c++;
            const cs = cell.colSpan || 1, rs = cell.rowSpan || 1;
            for (let dr = 0; dr < rs; dr++) {
                for (let dc = 0; dc < cs; dc++) {
                    if (!grid[r + dr]) grid[r + dr] = [];
                    grid[r + dr][c + dc] = { cell, anchor: dr === 0 && dc === 0 };
                }
            }
            c += cs;
        });
    });
    return grid;
}

/** 找 _tblCell 在逻辑网格里的锚点坐标 (r,c) 与所在 table/grid。 */
function _tblPos() {
    const table = _tblCell && _tblCell.closest('table');
    if (!table) return null;
    const grid = _tblBuildGrid(table);
    for (let r = 0; r < grid.length; r++) {
        for (let c = 0; c < (grid[r] || []).length; c++) {
            const g = grid[r][c];
            if (g && g.anchor && g.cell === _tblCell) return { table, grid, r, c };
        }
    }
    return null;
}

function _tblNewCell(tag, text) {
    const el = document.createElement(tag || 'td');
    el.textContent = text || '';
    return el;
}

function _tblInsertRow(below) {
    const tr = _tblCell.closest('tr');
    if (!tr) return;
    const clone = tr.cloneNode(true);
    Array.from(clone.cells).forEach(c => { c.textContent = ''; c.rowSpan = 1; });
    if (below) tr.after(clone); else tr.before(clone);
}

function _tblDeleteRow() {
    const tr = _tblCell.closest('tr');
    if (!tr) return;
    const table = tr.closest('table');
    if (table && table.rows.length <= 1) { showToast('至少保留一行', 'warning'); return; }
    _tblCell = null;
    tr.remove();
    _tblToolbar.style.display = 'none';
}

function _tblInsertCol(right) {
    const pos = _tblPos();
    if (!pos) return;
    const targetC = right ? pos.c + (_tblCell.colSpan || 1) : pos.c;
    Array.from(pos.table.rows).forEach((tr, r) => {
        // 找该行中覆盖第 targetC 逻辑列的单元格
        const g = pos.grid[r] && pos.grid[r][targetC];
        const isHead = tr.parentElement && tr.parentElement.tagName === 'THEAD';
        const nc = _tblNewCell(isHead ? 'th' : 'td', '');
        if (!g) { tr.appendChild(nc); return; }         // 该列在本行末尾之后
        if (g.anchor) { g.cell.before(nc); }            // 恰好是某单元格起点：插到它前面
        else { g.cell.colSpan = (g.cell.colSpan || 1) + 1; } // 落在跨列单元格内部：扩展它
    });
}

function _tblDeleteCol() {
    const pos = _tblPos();
    if (!pos) return;
    const col = pos.c;
    const removed = new Set();
    Array.from(pos.table.rows).forEach((tr, r) => {
        const g = pos.grid[r] && pos.grid[r][col];
        if (!g || removed.has(g.cell)) return;
        removed.add(g.cell);
        if ((g.cell.colSpan || 1) > 1) g.cell.colSpan = g.cell.colSpan - 1;
        else g.cell.remove();
    });
    _tblCell = null;
    _tblToolbar.style.display = 'none';
}

function _tblMergeRight() {
    const next = _tblCell.nextElementSibling;
    if (!next) { showToast('右侧没有可合并的单元格', 'warning'); return; }
    _tblCell.colSpan = (_tblCell.colSpan || 1) + (next.colSpan || 1);
    const t = next.textContent.trim();
    if (t) _tblCell.textContent = (_tblCell.textContent.trim() + ' ' + t).trim();
    next.remove();
}

function _tblMergeDown() {
    const pos = _tblPos();
    if (!pos) return;
    const rBelow = pos.r + (_tblCell.rowSpan || 1);
    const g = pos.grid[rBelow] && pos.grid[rBelow][pos.c];
    if (!g || !g.anchor) { showToast('下方没有可直接合并的单元格', 'warning'); return; }
    _tblCell.rowSpan = (_tblCell.rowSpan || 1) + (g.cell.rowSpan || 1);
    const t = g.cell.textContent.trim();
    if (t) _tblCell.textContent = (_tblCell.textContent.trim() + ' ' + t).trim();
    g.cell.remove();
}

function _tblSplit() {
    const cs = _tblCell.colSpan || 1, rs = _tblCell.rowSpan || 1;
    if (cs <= 1 && rs <= 1) { showToast('该单元格未合并', 'warning'); return; }
    const tag = _tblCell.tagName.toLowerCase();
    // 先补回同一行右侧缺的列
    for (let i = 1; i < cs; i++) _tblCell.after(_tblNewCell(tag, ''));
    _tblCell.colSpan = 1;
    // 再补回下方各行缺的单元格
    if (rs > 1) {
        const pos = _tblPos();
        _tblCell.rowSpan = 1;
        if (pos) {
            for (let dr = 1; dr < rs; dr++) {
                const tr = pos.table.rows[pos.r + dr];
                if (!tr) continue;
                // 在该行找到逻辑列 pos.c 应插入的位置
                const g = pos.grid[pos.r + dr] && pos.grid[pos.r + dr][pos.c];
                const nc = _tblNewCell(tag, '');
                if (g && g.cell && g.cell.parentElement === tr) g.cell.before(nc);
                else tr.appendChild(nc);
            }
        }
    }
}

/* ============ AI 辅助写作（选中文字 → 指令 → Kimi 处理 → 替换/插入） ============ */
let _aiPanelReady = false;
let _aiRange = null;      // 打开面板时保存的编辑区选区
let _aiPanel = null;

function _ensureAIPanel() {
    if (_aiPanelReady) return;
    _aiPanelReady = true;
    const p = document.createElement('div');
    p.className = 'ai-panel';
    p.style.display = 'none';
    p.innerHTML = `
        <div class="ai-panel-head">
            <span>✨ AI 辅助写作</span>
            <button type="button" class="ai-x" title="关闭">✕</button>
        </div>
        <div class="ai-sel" id="aiSelInfo"></div>
        <div class="ai-chips">
            <button type="button" data-ins="把这段文字润色得更通顺、更正式，保持原意">润色</button>
            <button type="button" data-ins="把这段文字改写成正式的申报材料书面语">改写更书面</button>
            <button type="button" data-ins="在保持原意的前提下适当扩写、补充细节">扩写</button>
            <button type="button" data-ins="在保持关键信息的前提下精简这段文字">精简</button>
            <button type="button" data-ins="检查并修正这段文字的语病、错别字和标点">纠错</button>
        </div>
        <textarea id="aiInstruction" class="ai-instruction" rows="2" placeholder="在此输入指令，例如：结合下方素材写一段项目概况 / 改写成更正式的语气 / 补充风险提示…"></textarea>
        <div class="ai-materials" style="margin:6px 0;">
            <div id="aiMatToggle" style="cursor:pointer;user-select:none;font-size:12px;color:#2563a6;">＋ 添加素材（粘贴文字 / 网页链接 / 上传文件，可选）</div>
            <div id="aiMatBody" style="display:none;margin-top:6px;">
                <div style="font-size:12px;color:#666;margin:4px 0 2px;">粘贴文字</div>
                <textarea id="aiPasted" class="ai-instruction" rows="3" placeholder="把参考资料/原始素材粘贴到这里…"></textarea>
                <div style="font-size:12px;color:#666;margin:6px 0 2px;">网页链接（每行一个）</div>
                <textarea id="aiUrls" class="ai-instruction" rows="2" placeholder="https://…（一行一个链接，AI 会读取网页正文）"></textarea>
                <div style="font-size:12px;color:#666;margin:6px 0 2px;">上传文件（Word / PPT / Excel / PDF / 图片，可多选）</div>
                <input type="file" id="aiFiles" multiple accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.pdf,.png,.jpg,.jpeg,.txt,.md,.csv" style="font-size:12px;" />
                <div id="aiFilesList" style="font-size:12px;color:#444;margin-top:4px;"></div>
            </div>
        </div>
        <div class="ai-actions">
            <button type="button" class="btn btn-primary btn-sm" id="aiGenBtn">生成</button>
        </div>
        <div class="ai-result-wrap" id="aiResultWrap" style="display:none;">
            <div class="ai-result" id="aiResult" contenteditable="true"></div>
            <div class="ai-result-actions">
                <button type="button" class="btn btn-primary btn-sm" id="aiReplaceBtn">替换选区</button>
                <button type="button" class="btn btn-ghost btn-sm" id="aiInsertBtn">插入到选区后</button>
                <button type="button" class="btn btn-ghost btn-sm" id="aiCopyBtn">复制</button>
                <button type="button" class="btn btn-ghost btn-sm" id="aiRegenBtn">重新生成</button>
            </div>
        </div>`;
    document.body.appendChild(p);
    // 素材展开后面板可能变高，限制高度并允许内部滚动，避免超出屏幕
    p.style.maxHeight = '85vh';
    p.style.overflowY = 'auto';
    _aiPanel = p;

    p.querySelector('.ai-x').addEventListener('click', () => { p.style.display = 'none'; });
    p.querySelectorAll('.ai-chips button').forEach(b => {
        b.addEventListener('click', () => {
            const ta = document.getElementById('aiInstruction');
            ta.value = b.dataset.ins;
            _aiGenerate();
        });
    });
    // 素材区：展开/收起 + 上传文件列表
    const matToggle = document.getElementById('aiMatToggle');
    const matBody = document.getElementById('aiMatBody');
    matToggle.addEventListener('click', () => {
        const open = matBody.style.display === 'none';
        matBody.style.display = open ? 'block' : 'none';
        matToggle.textContent = (open ? '－' : '＋') + ' 添加素材（粘贴文字 / 网页链接 / 上传文件，可选）';
    });
    document.getElementById('aiFiles').addEventListener('change', (e) => {
        const names = Array.from(e.target.files || []).map(f => f.name);
        document.getElementById('aiFilesList').textContent =
            names.length ? ('已选 ' + names.length + ' 个：' + names.join('、')) : '';
    });
    document.getElementById('aiGenBtn').addEventListener('click', () => _aiGenerate());
    document.getElementById('aiRegenBtn').addEventListener('click', () => _aiGenerate());
    document.getElementById('aiReplaceBtn').addEventListener('click', () => _aiApply(true));
    document.getElementById('aiInsertBtn').addEventListener('click', () => _aiApply(false));
    document.getElementById('aiCopyBtn').addEventListener('click', () => {
        const t = document.getElementById('aiResult').innerText;
        navigator.clipboard && navigator.clipboard.writeText(t);
        showToast('已复制');
    });
}

function openAIAssist() {
    _ensureAIPanel();
    // 捕获插入锚点：优先当前实时选区（在编辑区内），退回到最近记录的编辑区选区。
    // 折叠的光标也要保留——这样"未选中文字"时仍能插到光标处。
    let rng = null;
    const sel = window.getSelection();
    if (sel && sel.rangeCount) {
        let node = sel.getRangeAt(0).startContainer;
        if (node && node.nodeType === 3) node = node.parentElement;
        if (node && node.closest && node.closest('.doc-editor')) {
            rng = sel.getRangeAt(0).cloneRange();
        }
    }
    if (!rng && _lastEditorRange) rng = _lastEditorRange.cloneRange();
    _aiRange = rng;
    const selText = (_aiRange && !_aiRange.collapsed) ? _aiRange.toString().trim() : '';
    const info = document.getElementById('aiSelInfo');
    if (selText) {
        const short = selText.length > 60 ? selText.slice(0, 60) + '…' : selText;
        info.textContent = `已选中 ${selText.length} 字：${short}`;
        info.dataset.text = selText;
    } else {
        info.textContent = '未选中文字——将按指令直接生成，可插入到光标处。';
        info.dataset.text = '';
    }
    document.getElementById('aiResultWrap').style.display = 'none';
    document.getElementById('aiResult').textContent = '';
    // 重置素材区（避免上次的文字/链接/文件残留）
    ['aiPasted', 'aiUrls'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const fi = document.getElementById('aiFiles'); if (fi) fi.value = '';
    const fl = document.getElementById('aiFilesList'); if (fl) fl.textContent = '';
    const mb = document.getElementById('aiMatBody'); if (mb) mb.style.display = 'none';
    const mt = document.getElementById('aiMatToggle');
    if (mt) mt.textContent = '＋ 添加素材（粘贴文字 / 网页链接 / 上传文件，可选）';
    _aiPanel.style.display = 'block';
    const ta = document.getElementById('aiInstruction');
    ta.value = '';
    ta.focus();
}

async function _aiGenerate() {
    const instruction = document.getElementById('aiInstruction').value.trim();
    if (!instruction) { showToast('请先输入或选择一个指令', 'warning'); return; }
    const text = document.getElementById('aiSelInfo').dataset.text || '';
    const pasted = (document.getElementById('aiPasted')?.value || '').trim();
    const urls = (document.getElementById('aiUrls')?.value || '').trim();
    const fileInput = document.getElementById('aiFiles');
    const files = (fileInput && fileInput.files) ? Array.from(fileInput.files) : [];
    const hasMaterials = !!(pasted || urls || files.length);
    const btn = document.getElementById('aiGenBtn');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = hasMaterials ? '读取素材并生成中…' : '生成中…';
    try {
        let r;
        if (hasMaterials) {
            const fd = new FormData();
            fd.append('instruction', instruction);
            fd.append('selected_text', text);
            fd.append('pasted_text', pasted);
            fd.append('urls', urls);
            files.forEach(f => fd.append('files', f, f.name));
            r = await API.aiCompose(fd);
        } else {
            r = await API.aiEdit(text, instruction);
        }
        document.getElementById('aiResult').textContent = r.result || '';
        document.getElementById('aiResultWrap').style.display = 'block';
    } catch (e) {
        showToast('AI处理失败：' + (e.message || e), 'error');
    } finally {
        btn.disabled = false; btn.textContent = old;
    }
}

function _textToFragment(text) {
    const frag = document.createDocumentFragment();
    const parts = String(text).split('\n');
    parts.forEach((line, i) => {
        if (i > 0) frag.appendChild(document.createElement('br'));
        frag.appendChild(document.createTextNode(line));
    });
    return frag;
}

function _aiApply(replace) {
    const text = document.getElementById('aiResult').innerText.trim();
    if (!text) { showToast('没有可用的结果', 'warning'); return; }
    if (!_aiRange) {
        showToast('请先回到正文里选中一段文字（或点一下放置光标），再打开AI辅助', 'warning');
        return;
    }
    const range = _aiRange;
    if (replace && !range.collapsed) range.deleteContents(); // 有选区才替换；无选区则等同插入到光标
    else range.collapse(false);                              // 折叠到选区末尾，插到其后
    range.insertNode(_textToFragment(text));
    range.collapse(false);  // 光标落到插入内容之后，便于连续操作
    _aiPanel.style.display = 'none';
    showToast(replace ? '已替换，记得点“保存”' : '已插入，记得点“保存”');
}

/** 在当前光标处插入脚注 */
function insertFootnote() {
    const range = _lastEditorRange;
    let editorNode = range && range.startContainer;
    if (editorNode && editorNode.nodeType === 3) editorNode = editorNode.parentElement;
    const editor = editorNode && editorNode.closest && editorNode.closest('.doc-editor');
    if (!range || !editor) {
        showToast('请先把光标放到正文中要插入脚注的位置', 'warning');
        return;
    }
    const text = prompt('请输入脚注内容：');
    if (text === null || !text.trim()) return;

    const sup = document.createElement('sup');
    sup.className = 'fn-ref';
    sup.setAttribute('contenteditable', 'false');
    sup.dataset.fn = text.trim();
    sup.textContent = '?';

    range.collapse(false);        // 插到选区末尾/光标处
    range.insertNode(sup);
    range.setStartAfter(sup);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);

    _renumberFootnotes();
    showToast('已插入脚注，保存后写入 Word（双击脚注可改/删）');
}

/** 按文档顺序给所有脚注重新编号（仅显示用，Word 里最终自动编号），并刷新底部面板 */
function _renumberFootnotes() {
    const sups = document.querySelectorAll('#chapterDetail .doc-editor sup.fn-ref');
    let i = 0;
    sups.forEach(s => {
        i += 1;
        if (!s.dataset.fnid) s.dataset.fnid = 'fn' + Date.now() + '_' + i;
        s.textContent = i;
        s.title = s.dataset.fn || '';
    });
    _lastFootnoteCount = sups.length;   // 记录当前脚注数，供 input 监听判断增减
    _renderFootnotePanel(sups);
}

/** 编辑区下方的实时脚注面板：编号 + 可直接编辑的文字 + 删除 */
function _renderFootnotePanel(sups) {
    const panel = document.getElementById('ch1FootnotePanel');
    if (!panel) return;
    sups = sups || document.querySelectorAll('#chapterDetail .doc-editor sup.fn-ref');
    if (!sups.length) {
        panel.style.display = 'none';
        panel.innerHTML = '';
        return;
    }
    let rows = '';
    let i = 0;
    sups.forEach(s => {
        i += 1;
        const fnid = s.dataset.fnid;
        rows += `
            <div class="fn-row" data-fnid="${fnid}">
                <span class="fn-row-num">${i}</span>
                <span class="fn-row-text" contenteditable="true"
                      onblur="syncFootnoteText('${fnid}', this)">${_escHtmlAttr(s.dataset.fn || '')}</span>
                <button class="fn-row-del" title="删除该脚注"
                        onclick="deleteFootnoteById('${fnid}')">✕</button>
            </div>`;
    });
    panel.innerHTML = `<div class="fn-panel-title">脚注（保存后写入 Word 页面底部）</div>
        <div class="fn-panel-list">${rows}</div>`;
    panel.style.display = '';
}

/** 面板里改脚注文字 -> 同步回正文里的脚注标记 */
function syncFootnoteText(fnid, el) {
    const sup = document.querySelector(`#chapterDetail sup.fn-ref[data-fnid="${fnid}"]`);
    if (!sup) return;
    const v = (el.textContent || '').trim();
    if (!v) { deleteFootnoteById(fnid); return; }
    sup.dataset.fn = v;
    sup.title = v;
}

/** 面板里删除脚注 */
function deleteFootnoteById(fnid) {
    const sup = document.querySelector(`#chapterDetail sup.fn-ref[data-fnid="${fnid}"]`);
    if (sup) sup.remove();
    _renumberFootnotes();
}

// ===== 画图（draw.io） =====
function _b64EncodeStr(s) { return btoa(unescape(encodeURIComponent(s || ''))); }
function _b64DecodeStr(b64) {
    try { return decodeURIComponent(escape(atob(b64 || ''))); } catch (e) { return ''; }
}

/** 图块内部：删除按钮 + 图片（删除按钮 UI-only，保存时会被后端解析器跳过） */
function _diagramInner(pngB64) {
    return `<button class="doc-diagram-del" type="button" title="删除此图" contenteditable="false">✕</button>`
        + `<img src="data:image/png;base64,${pngB64}" alt="框图">`;
}

/** 图块 HTML：data-png 存 PNG(base64) 给 Word/显示，data-xml 存源码供重编辑 */
function _diagramBlockHTML(pngB64, xmlB64) {
    return `<div class="doc-diagram" contenteditable="false" `
        + `data-png="${pngB64}" data-xml="${xmlB64}">${_diagramInner(pngB64)}</div>`;
}

/** 载入后：按 data-png 重建图块内部（图片 + 删除按钮），保证都有删除按钮 */
function _renderDiagramPlaceholders() {
    document.querySelectorAll('#chapterDetail .doc-diagram').forEach(div => {
        if (div.dataset.png) div.innerHTML = _diagramInner(div.dataset.png);
    });
}

/** 点"画图"：先选模板，再打开 draw.io，保存后把图块插入到光标处 */
function insertDiagram() {
    const range = _lastEditorRange;
    let node = range && range.startContainer;
    if (node && node.nodeType === 3) node = node.parentElement;
    const editor = node && node.closest && node.closest('.doc-editor');
    if (!range || !editor) {
        showToast('请先把光标放到正文中要插入图的位置', 'warning');
        return;
    }
    _pickDiagramTemplate((xml) => {
        DrawioEditor.open(xml, ({ png, xml }) => {
            const pngB64 = (png.split(',')[1]) || '';
            const wrap = document.createElement('div');
            wrap.innerHTML = _diagramBlockHTML(pngB64, _b64EncodeStr(xml));
            const block = wrap.firstElementChild;
            range.collapse(false);
            range.insertNode(block);
            const p = document.createElement('p');
            p.innerHTML = '<br>';
            block.after(p);
            showToast('已插入图，保存后写入 Word（双击图可再编辑）');
        });
    });
}

/** 双击正文里的图块 -> 用存的源码重新打开 draw.io 编辑 */
function _editDiagramBlock(div) {
    const xml = _b64DecodeStr(div.dataset.xml || '') || null;
    DrawioEditor.open(xml, ({ png, xml }) => {
        const pngB64 = (png.split(',')[1]) || '';
        div.dataset.png = pngB64;
        div.dataset.xml = _b64EncodeStr(xml);
        div.innerHTML = _diagramInner(pngB64);
    });
}

/** 模板选择器：弹小窗列出"空白 + 各模板"，选中后回调 cb(xml|null) */
async function _pickDiagramTemplate(cb) {
    let templates = [];
    try { templates = await API.getDiagramTemplates(); } catch (e) { /* 无模板则只给空白 */ }

    const overlay = document.createElement('div');
    overlay.className = 'tpl-overlay';
    const cards = [`<div class="tpl-card" data-name="">
            <div class="tpl-thumb tpl-blank">＋</div><div class="tpl-name">空白</div></div>`]
        .concat((templates || []).map(t => `
            <div class="tpl-card" data-name="${_escHtmlAttr(t.name)}">
                <div class="tpl-thumb">${t.thumb ? `<img src="${t.thumb}">` : '🖼'}</div>
                <div class="tpl-name">${_escHtmlAttr(t.label || t.name)}</div>
            </div>`)).join('');
    overlay.innerHTML = `<div class="tpl-modal">
            <div class="tpl-head">选择画图模板<button class="tpl-x">✕</button></div>
            <div class="tpl-grid">${cards}</div>
        </div>`;
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    overlay.addEventListener('mousedown', e => { if (e.target === overlay) close(); });
    overlay.querySelector('.tpl-x').addEventListener('click', close);
    overlay.querySelectorAll('.tpl-card').forEach(card => {
        card.addEventListener('click', async () => {
            const name = card.dataset.name;
            close();
            if (!name) { cb(null); return; }
            try {
                const resp = await API.getDiagramTemplate(name);
                cb(resp.xml || null);
            } catch (e) {
                showToast('加载模板失败：' + e.message, 'error');
                cb(null);
            }
        });
    });
}

/**
 * 底部整体进度：当前项目绑定包中有内容的章节占比（source 非 template 视为有内容）
 */
async function updateChapterProgress() {
    const fill = document.getElementById('progressFill');
    const label = document.getElementById('progressPercent');
    if (!fill || !label) return;

    const chapters = PACK_CHAPTERS || [];
    if (!currentProjectId || chapters.length === 0) {
        fill.style.width = '0%';
        label.textContent = '0%';
        return;
    }

    const results = await Promise.all(chapters.map(async (ch) => {
        try {
            const d = await API.getChapterContent(ch.n);
            return !!(d && d.source && d.source !== 'template');
        } catch (e) {
            return false;
        }
    }));
    const done = results.filter(Boolean).length;
    const percent = Math.round(done / chapters.length * 100);
    fill.style.width = percent + '%';
    label.textContent = percent + '%';
}

// ===== 文档管理页面功能 =====

/**
 * 加载文档列表（新管线：列出当前项目已生成的各章 Word）
 */
async function loadDocuments() {
    const tbody = document.querySelector('#docTable tbody');
    if (!tbody) return;

    if (!currentProjectId) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px">请先选择项目</td></tr>';
        return;
    }

    try {
        const result = await API.getDocuments();
        const docs = (result && result.documents) || [];
        if (docs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px">暂无已生成文档（请先在材料生成页预览/生成章节）</td></tr>';
            return;
        }
        tbody.innerHTML = docs.map(doc => `
            <tr>
                <td><strong>${_escHtmlAttr(doc.title)}</strong><br><span class="text-muted text-sm">${_escHtmlAttr(doc.filename)}</span></td>
                <td>${_escHtmlAttr(_fmtTime(doc.updated_at))}</td>
                <td>${_escHtmlAttr(doc.size_formatted)}</td>
                <td><span class="badge badge-success">已完成</span></td>
                <td><button class="btn btn-primary btn-sm" onclick="API.downloadChapterDocx(${doc.chapter})">下载</button></td>
            </tr>
        `).join('');
    } catch (error) {
        console.warn('[REIT-AI] 加载文档列表失败:', error.message);
    }
}

function switchDocTab(tab) {
    document.querySelectorAll('#docTabBar .tab-item').forEach(item => {
        item.classList.remove('active');
    });
    event.target.classList.add('active');
    // 新管线文档列表无 tab 维度差异（无历史版本概念），统一重新拉取
    if (currentProjectId) {
        loadDocuments();
    }
}

// ===== 项目创建 =====

/**
 * 打开"新建项目"弹窗：材料模板下拉从 /api/packs 拉取，默认选中默认包
 */
async function openNewProjectModal() {
    const nameInput = document.getElementById('newProjectName');
    const packSel = document.getElementById('newProjectPack');
    if (nameInput) nameInput.value = '';
    clearLocalFolder();  // 重置上次选的本地文件夹
    if (packSel) {
        packSel.innerHTML = '<option value="">加载中…</option>';
        try {
            const r = await API.getPacks();
            const packs = r.packs || [];
            packSel.innerHTML = packs.map(p =>
                `<option value="${_escHtmlAttr(p.id)}"${p.id === r.default_id ? ' selected' : ''}>`
                + `${_escHtmlAttr(p.name || p.id)}${p.version ? '（v' + _escHtmlAttr(p.version) + '）' : ''}</option>`
            ).join('');
            if (!packs.length) {
                packSel.innerHTML = '<option value="">（templates-packs 下没有可用模板包）</option>';
            }
        } catch (e) {
            packSel.innerHTML = '<option value="">获取模板包失败</option>';
        }
    }
    openModal('modal-new-project');
}

// ===== 本地文件夹选择：直接选用户自己电脑上的文件夹（含桌面），
// 创建项目成功后自动把里面的文件上传为申报材料 =====

let _pickedFolderFiles = [];  // 选中的本地文件列表（File[]）

/** 打开浏览器原生文件夹选择器 */
function openLocalFolderPicker() {
    const input = document.getElementById('localFolderInput');
    if (input) { input.value = ''; input.click(); }
}

/** 选完本地文件夹：暂存文件列表，摘要框显示文件夹名与文件数 */
function onLocalFolderPicked(fileList) {
    const files = Array.from(fileList || []);
    const summary = document.getElementById('localFolderSummary');
    if (!files.length) { _pickedFolderFiles = []; if (summary) summary.value = ''; return; }
    const rel = files[0].webkitRelativePath || '';
    const folderName = rel ? rel.split('/')[0] : '已选文件夹';
    _pickedFolderFiles = files;
    if (summary) summary.value = `📁 ${folderName} · 共 ${files.length} 个文件（创建后自动上传）`;
}

/** 清空本地文件夹选择 */
function clearLocalFolder() {
    _pickedFolderFiles = [];
    const input = document.getElementById('localFolderInput');
    const summary = document.getElementById('localFolderSummary');
    if (input) input.value = '';
    if (summary) summary.value = '';
}

// ===== 新建项目弹窗：上传中可收起为跳动小圆圈，点击找回进度 =====
let _npUploadBusy = false;   // 新建项目弹窗是否正在创建/上传

/** 点“—”收起：上传中会被观察器自动缩成小圆圈；非上传时等同关闭 */
function minimizeNewProjectModal() {
    closeModal('modal-new-project');
}

/** 获取/创建上传进度小圆圈（右下角固定、脉动） */
function _getUploadBubble() {
    let b = document.getElementById('uploadBubble');
    if (!b) {
        b = document.createElement('div');
        b.id = 'uploadBubble';
        b.className = 'upload-bubble';
        b.title = '正在上传申报材料，点击查看进度';
        b.innerHTML = '<span class="ub-ico">⬆️</span><span class="ub-pct">0%</span>';
        b.addEventListener('click', () => {
            b.style.display = 'none';
            openModal('modal-new-project');
        });
        document.body.appendChild(b);
    }
    return b;
}
function showUploadBubble() { _getUploadBubble().style.display = 'flex'; }
function hideUploadBubble() { const b = document.getElementById('uploadBubble'); if (b) b.style.display = 'none'; }
function _updateBubble(pct) {
    const b = document.getElementById('uploadBubble');
    if (b && b.style.display !== 'none') {
        const p = b.querySelector('.ub-pct');
        if (p) p.textContent = Math.round(pct) + '%';
    }
}

/** 监听新建项目弹窗：上传中被任何方式关闭（点外部/ESC/✕/取消/—）都自动缩成小圆圈 */
function _watchNewProjectModalHide() {
    const modal = document.getElementById('modal-new-project');
    if (!modal || modal.dataset.hideWatched) return;
    modal.dataset.hideWatched = '1';
    new MutationObserver(() => {
        if (!modal.classList.contains('show') && _npUploadBusy) showUploadBubble();
    }).observe(modal, { attributes: true, attributeFilter: ['class'] });
}

/** 时长格式化：X秒 / X分Y秒 / X小时Y分 */
function _fmtDur(sec) {
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `${sec}秒`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return s ? `${m}分${s}秒` : `${m}分钟`;
    return `${Math.floor(m / 60)}小时${m % 60}分`;
}

/**
 * 提交新建项目：名称 + 可选本地文件夹（创建成功后分批上传其中的文件，
 * 弹窗内实时显示进度，避免用户以为卡住）
 */
async function submitNewProject() {
    const name = (document.getElementById('newProjectName').value || '').trim();
    const packId = document.getElementById('newProjectPack').value || '';
    if (!name) { showToast('请填写项目名称', 'warning'); return; }

    const btn = document.getElementById('btnSubmitNewProject');
    const progress = document.getElementById('newProjectProgress');
    const pText = document.getElementById('newProjectProgressText');
    const pBar = document.getElementById('newProjectProgressBar');
    const setProgress = (text, pct) => {
        if (pText) pText.textContent = text;
        if (pBar) pBar.style.width = Math.max(0, Math.min(100, Math.round(pct))) + '%';
        _updateBubble(pct);
    };
    if (btn) { btn.disabled = true; btn.textContent = '处理中…'; }
    if (progress) progress.style.display = 'block';
    _npUploadBusy = true;

    try {
        setProgress('正在创建项目…', 5);
        const created = await API.createProject(name, '', packId || undefined);
        if (_pickedFolderFiles.length) {
            // 分批上传（每批 15 个文件），逐批刷新进度条与耗时预估
            const files = _pickedFolderFiles;
            const BATCH = 15;
            let uploaded = 0, skipped = 0;
            let skippedNames = [];
            const t0 = Date.now();
            currentProjectId = created.id;  // uploadMaterials 按当前项目上传
            try {
                for (let i = 0; i < files.length; i += BATCH) {
                    const batch = files.slice(i, i + BATCH);
                    const to = Math.min(i + BATCH, files.length);
                    const result = await API.uploadMaterials(batch);
                    uploaded += (result.uploaded || []).length;
                    skipped += (result.skipped || []).length;
                    skippedNames = skippedNames.concat(result.skipped || []);
                    // 已用时长 + 按已传均速估算剩余时间
                    const elapsed = (Date.now() - t0) / 1000;
                    const eta = (to > 0 && elapsed > 3) ? (files.length - to) * elapsed / to : null;
                    const tip = eta === null
                        ? `｜已用 ${_fmtDur(elapsed)}，正在估算时间…`
                        : `｜已用 ${_fmtDur(elapsed)}，预计还需 ${_fmtDur(eta)}`;
                    setProgress(`正在上传申报材料（第 ${to}/${files.length} 个文件）…${tip}`, 5 + 90 * to / files.length);
                }
                setProgress('上传完成', 100);
                showToast(`完成：已上传 ${uploaded} 份材料${skipped ? `，${skipped} 个不支持类型的文件已跳过：${skippedNames.slice(0, 3).join('、')}${skippedNames.length > 3 ? ' 等' : ''}` : ''}`);
            } catch (ue) {
                showToast('项目已创建，但部分材料上传失败：' + (ue.message || '') + '；可到「系统设置 → 申报材料」重传', 'error');
            }
            clearLocalFolder();
        } else {
            setProgress('创建完成', 100);
            showToast('项目创建成功，可随时到「系统设置 → 申报材料」上传您电脑里的文件');
        }
        closeModal('modal-new-project');
        await loadOverviewData();
    } catch (e) {
        showToast('创建失败：' + (e.message || '未知错误'), 'error');
    } finally {
        _npUploadBusy = false;
        hideUploadBubble();
        if (btn) { btn.disabled = false; btn.textContent = '创 建项目'; }
        if (progress) progress.style.display = 'none';
        if (pBar) pBar.style.width = '0';
    }
}

/**
 * 选择项目并进入发改委材料生成页
 * @param {number} projectId - 项目ID
 */
function selectProject(projectId) {
    currentProjectId = projectId;
    updateProjectHeaderBar();
    navigate('ndrc');
    loadMaterialsUI();  // 面板默认展开态，toggle 不会触发加载，这里主动刷新
}

/** 删除项目（确认后调后端；示范项目不展示删除按钮，后端另有 403 保护） */
async function confirmDeleteProject(projectId) {
    const proj = (_projectsCache || []).find(p => p.id === projectId);
    const name = proj ? proj.name : `项目 ${projectId}`;
    if (!confirm(`确定删除项目“${name}”？\n删除将同时清除该项目已上传的材料和已生成的内容，且不可恢复。`)) return;
    try {
        await API.deleteProject(projectId);
        if (currentProjectId === projectId) currentProjectId = null;
        showToast('项目已删除');
        await loadOverviewData();
    } catch (e) {
        showToast('删除失败：' + (e.message || '未知错误'), 'error');
    }
}

/**
 * 初始化应用
 */
// ===== 登录认证（步骤 3.2）=====

/** 弹出登录层（msg 为可选的提示，如"登录已过期"） */
function showLoginOverlay(msg) {
    const overlay = document.getElementById('login-overlay');
    const errBox = document.getElementById('loginError');
    if (overlay) overlay.classList.remove('hidden');
    if (errBox) {
        errBox.style.display = msg ? 'block' : 'none';
        errBox.textContent = msg || '';
    }
    const input = document.getElementById('loginPassword');
    if (input) { input.value = ''; input.focus(); }
}

/** 隐藏登录层 */
function hideLoginOverlay() {
    const overlay = document.getElementById('login-overlay');
    if (overlay) overlay.classList.add('hidden');
}

/** 提交登录表单 */
async function doLogin() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    const errBox = document.getElementById('loginError');
    const btn = document.getElementById('loginBtn');
    if (!username || !password) {
        errBox.textContent = '请输入用户名和密码';
        errBox.style.display = 'block';
        return;
    }
    btn.disabled = true;
    btn.textContent = '登录中...';
    try {
        const result = await API.login(username, password);
        AuthToken.set(result.token);
        hideLoginOverlay();
        if (result.must_change_password) {
            // 首次登录强制改密：改完再拉数据
            document.getElementById('modal-change-password').classList.add('show');
        } else {
            await continueInit();
        }
    } catch (e) {
        errBox.textContent = e.message || '登录失败';
        errBox.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = '登 录';
    }
}

/** 提交改密（首次登录强制改密也走这里），成功后刷新页面重新初始化 */
async function doChangePassword() {
    const oldPwd = document.getElementById('cpOld').value;
    const newPwd = document.getElementById('cpNew').value;
    const confirm = document.getElementById('cpConfirm').value;
    const errBox = document.getElementById('cpError');
    errBox.style.display = 'none';
    if (!oldPwd || !newPwd) {
        errBox.textContent = '请填写完整';
        errBox.style.display = 'block';
        return;
    }
    if (newPwd !== confirm) {
        errBox.textContent = '两次输入的新密码不一致';
        errBox.style.display = 'block';
        return;
    }
    try {
        await API.changePassword(oldPwd, newPwd);
        showToast('密码修改成功', 'success');
        setTimeout(() => location.reload(), 600);
    } catch (e) {
        errBox.textContent = e.message || '修改失败';
        errBox.style.display = 'block';
    }
}

/** 登录成功后的数据初始化（原 initApp 的项目加载部分） */
async function continueInit() {
    try {
        const projects = await API.getProjects();
        _projectsCache = projects || [];
        if (projects && projects.length > 0) {
            currentProjectId = projects[0].id;
            console.log('[REIT-AI] 已自动选择项目:', projects[0].name, 'ID=', currentProjectId);
            // 预拉绑定包信息，侧边栏/页面标题能立即显示材料模板名称
            loadProjectPack();
        }
        updateProjectHeaderBar();
    } catch (error) {
        console.warn('[REIT-AI] 项目数据加载失败:', error.message);
    }
    navigate('overview');
}

/** 退出登录（总复查补遗）：清除本地 token 后回到登录层。 */
function logout() {
    AuthToken.clear();
    location.reload();
}

async function initApp() {
    console.log('[REIT-AI] 系统初始化中...');

    // 绑定全局事件（登录表单支持回车提交）
    bindGlobalEvents();
    document.getElementById('loginPassword').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') doLogin();
    });

    // 检查登录态（步骤 3.2）：无 token 或 token 失效 → 停在登录层
    if (!AuthToken.get()) {
        showLoginOverlay();
        return;
    }
    try {
        const me = await API.getMe();
        console.log('[REIT-AI] 已登录用户:', me.username);
        hideLoginOverlay();
        // 概览页欢迎横幅：按用户名个性化问候
        const heroGreeting = document.getElementById('heroGreeting');
        if (heroGreeting && me.username) heroGreeting.textContent = `${me.username}，欢迎回来 👋`;
        if (me.must_change_password) {
            document.getElementById('modal-change-password').classList.add('show');
            return;
        }
    } catch (e) {
        // getMe 401 时 api.js 已弹登录层
        return;
    }

    // 后端健康检查
    try {
        const health = await API.get('/health');
        console.log('[REIT-AI] 后端服务连接正常:', health);
    } catch (error) {
        console.warn('[REIT-AI] 后端服务未就绪:', error.message);
    }

    await continueInit();
    console.log('[REIT-AI] 系统初始化完成');
}

/**
 * 绑定全局事件监听器
 */
function bindGlobalEvents() {
    _watchNewProjectModalHide();  // 上传中弹窗被关 → 自动缩成小圆圈
    // 点击弹窗外部关闭（data-sticky 的强制弹窗除外，如首次登录改密）
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        if (overlay.dataset.sticky === 'true') return;
        overlay.addEventListener('click', function (e) {
            if (e.target === this) {
                this.classList.remove('show');
            }
        });
    });

    // ESC 关闭弹窗（sticky 弹窗除外）
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-overlay.show').forEach(modal => {
                if (modal.dataset.sticky === 'true') return;
                modal.classList.remove('show');
            });
        }
    });
}

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', initApp);
