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

    // 进设置页时刷新当前项目的材料列表（步骤 3.4）
    if (pageId === 'settings') {
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

/** 加载 Kimi 模型下拉：列出 key 可用的模型，选中当前使用的 */
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
        sel.innerHTML = models.map(m =>
            `<option value="${_escHtmlAttr(m)}"${m === current ? ' selected' : ''}>${_escHtmlAttr(m)}</option>`
        ).join('');
    } catch (e) {
        sel.innerHTML = `<option value="">获取失败：${_escHtmlAttr(e.message)}</option>`;
    }
}

/** 保存所选 Kimi 模型（即时生效，各章生成都用它） */
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

/** 拉取当前项目的材料列表并渲染（设置页展示） */
async function loadMaterialsUI() {
    const stat = document.getElementById('materialsStat');
    const list = document.getElementById('materialsFileList');
    if (!stat || !list) return;
    try {
        const data = await API.listMaterials();
        stat.textContent = data.total_files > 0
            ? `已上传 ${data.total_files} 个文件，共 ${_fmtSize(data.total_size)}`
            : '尚未上传材料';
        if (data.total_files === 0) {
            list.innerHTML = '<div class="text-muted text-sm">暂无材料</div>';
            return;
        }
        list.innerHTML = data.files.map(f =>
            `<div style="display:flex;justify-content:space-between;gap:8px;padding:3px 4px;font-size:13px">
                <span style="word-break:break-all">📄 ${f.path}</span>
                <span class="text-muted" style="flex-shrink:0">${_fmtSize(f.size)}</span>
            </div>`).join('');
    } catch (e) {
        stat.textContent = '材料列表加载失败';
    }
}

/** 选择文件后上传（支持多选，zip 后端自动解压） */
async function onUploadMaterials(input) {
    const files = input.files;
    if (!files || files.length === 0) return;
    const stat = document.getElementById('materialsStat');
    const oldText = stat ? stat.textContent : '';
    try {
        if (stat) stat.textContent = `正在上传 ${files.length} 个文件…（大文件/解压需要一点时间）`;
        const result = await API.uploadMaterials(files);
        const parts = [];
        if (result.uploaded && result.uploaded.length) parts.push(`直传 ${result.uploaded.length} 个`);
        if (result.extracted_from_zip) parts.push(`zip 解压出 ${result.extracted_from_zip} 个`);
        if (result.skipped && result.skipped.length) parts.push(`跳过不支持的格式 ${result.skipped.length} 个`);
        showToast('上传完成：' + (parts.join('，') || '无新增文件'));
        await loadMaterialsUI();
    } catch (e) {
        showToast('上传失败：' + e.message, 'error');
        if (stat) stat.textContent = oldText;
    } finally {
        input.value = '';  // 允许重复选择同一个文件
    }
}

/** 清空当前项目的全部材料（二次确认） */
async function clearMaterialsUI() {
    if (!confirm('确定清空当前项目的全部申报材料吗？此操作不可恢复。')) return;
    try {
        await API.clearMaterials();
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
                    updateTime: p.updated_at || p.created_at || '-',
                    id: p.id,
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
    metaEl.textContent = `状态：${_mapProjectStatus(proj.status)} | 更新时间：${proj.updated_at || proj.created_at || '-'}`;
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

/** 按绑定包刷新界面文案（侧边栏/页面标题里的材料类型名称） */
function _applyPackLabels() {
    const label = (PACK_INFO && PACK_INFO.name) ? PACK_INFO.name : '材料生成';
    const navLabel = document.getElementById('navMaterialLabel');
    if (navLabel) {
        navLabel.textContent = label;
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

    // 第一项固定为"摘要表和释义"（不属于章节，走独立的点击逻辑）
    const summaryStep = {
        title: '摘要表和释义',
        status: '',
        desc: '项目基础信息',
        circle: '摘',
        onClick: 'selectSummary()',
    };

    const chapterSteps = (PACK_CHAPTERS || []).map(ch => ({
        title: ch.title,
        status: '',
        desc: '待生成',
        onClick: `selectChapter(${ch.n})`,
    }));

    renderStepper(container, [summaryStep, ...chapterSteps]);
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
                <button class="btn btn-ghost btn-sm" id="btnChapterGen" onclick="runKimiChapter()">🤖 ${content.source === 'ready' ? '重新生成' : '用Kimi生成'}</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertFootnote()" title="把光标放到正文中要加脚注的位置，再点此">➕ 脚注</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertDiagram()" title="把光标放到正文中要插图的位置，再点此画框图">🖼 画图</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="openAIAssist()" title="先在正文里选中一段文字，再点此让AI润色/改写/扩写等">✨ AI辅助</button>
                <button class="btn btn-ghost btn-sm" id="btnChapterPreviewToggle" onclick="toggleChapterPreview()">📄 ${_previewOn ? '关闭Word预览' : '开启Word预览'}</button>
                <button class="btn btn-primary btn-sm" onclick="saveChapter()">💾 保存</button>
            </div>
        </div>
        <div id="chapterGenBanner" class="kimi-status" style="display:none;margin-bottom:12px"></div>`;

    // 左右分栏：左=编辑区，右=Word 预览（默认隐藏，由开启按钮控制）
    html += `<div class="ch1-split">
        <div class="ch1-editor-col">
            <div class="sections-tree">`;

    if (!hasSections) {
        html += `<div class="text-sm text-muted" style="padding:8px 0;">未读到本章小标题。请先到"系统设置"里选择官方模板文件路径——本章的小标题会按模板自动列在下面，每个小标题一个编辑区。</div>`;
    } else {
        if (content.source === 'template') {
            html += `<div class="text-sm text-muted" style="padding:4px 0 8px;">以下小标题来自官方模板。点右上角"用Kimi生成"自动填写，或直接在各小标题下编辑。</div>`;
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

/** 轮询生成进度；完成后重渲染当前章编辑视图 */
function _pollChapterGeneration() {
    const n = _editorChapter;
    const btn = document.getElementById('btnChapterGen');
    const banner = document.getElementById('chapterGenBanner');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中...'; }
    if (banner) { banner.style.display = 'block'; banner.textContent = `🤖 Kimi 正在生成${_chapterTitle(n) || '本章'}，约需数分钟，请稍候…`; }

    if (_kimiTimer) clearInterval(_kimiTimer);
    _kimiTimer = setInterval(async () => {
        let st;
        try { st = await API.getChapterStatus(n); } catch (e) { return; }
        if (st.status === 'done') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            delete _previewCache[n];   // 重新生成了，预览缓存作废
            await renderChapterEditor(n);
            showToast('生成完成，请核对编辑');
        } else if (st.status === 'error') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            if (btn) { btn.disabled = false; btn.textContent = '🤖 用Kimi生成'; }
            if (banner) { banner.className = 'kimi-status error'; banner.textContent = '生成失败：' + (st.error || '未知错误'); }
            showToast('生成失败', 'error');
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
                <td>${_escHtmlAttr(doc.updated_at)}</td>
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
    const pathInput = document.getElementById('newProjectDataSource');
    const packSel = document.getElementById('newProjectPack');
    if (nameInput) nameInput.value = '';
    if (pathInput) pathInput.value = '';
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

/**
 * 提交新建项目：名称 + 数据源文件夹 + 材料模板（后端校验路径与包）
 */
async function submitNewProject() {
    const name = (document.getElementById('newProjectName').value || '').trim();
    const path = (document.getElementById('newProjectDataSource').value || '').trim();
    const packId = document.getElementById('newProjectPack').value || '';
    if (!name) { showToast('请填写项目名称', 'warning'); return; }
    if (!path) { showToast('请选择数据源文件夹', 'warning'); return; }
    try {
        await API.createProject(name, path, packId || undefined);
        closeModal('modal-new-project');
        showToast('项目创建成功');
        await loadOverviewData();
    } catch (e) {
        showToast('创建失败：' + (e.message || '未知错误'), 'error');
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
