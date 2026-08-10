/**
 * REIT-AI 法律文件生成系统 - 主应用逻辑
 */

// 页面标题映射
const PAGE_TITLES = {
    'overview': 'REITS AI',
    'ndrc': '发改委材料生成',
    'chapter-edit': '章节编辑',
    'documents': '文档管理',
    'settings': '系统设置'
};

// 当前状态
let currentPage = 'overview';
let currentProjectId = null;
let currentChapter = 'chapter1';
let chaptersData = [];
let generationPollingTimer = null;

// 系统设置页里几个"选择文件"路径框：选中即通过 localStorage 自动保存，刷新页面后自动恢复
const SETTINGS_PATH_INPUT_IDS = ['settingOutputPath', 'settingTemplatePath', 'settingNdrcMaterialPath'];

function _persistSettingsPathIfNeeded(inputId, value) {
    if (SETTINGS_PATH_INPUT_IDS.includes(inputId)) {
        localStorage.setItem('reitai_' + inputId, value);
    }
}

function restoreSettingsPaths() {
    SETTINGS_PATH_INPUT_IDS.forEach(id => {
        const saved = localStorage.getItem('reitai_' + id);
        if (saved) {
            const el = document.getElementById(id);
            if (el) el.value = saved;
        }
    });
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

    // 更新侧边栏激活状态
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    // chapter-edit页面对应ndrc导航项
    const navPage = pageId === 'chapter-edit' ? 'ndrc' : pageId;
    const activeNav = document.querySelector(`.nav-item[data-page="${navPage}"]`);
    if (activeNav) {
        activeNav.classList.add('active');
    }

    // 更新页面标题
    const titleEl = document.getElementById('pageTitle');
    if (titleEl && PAGE_TITLES[pageId]) {
        titleEl.textContent = PAGE_TITLES[pageId];
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
            _syncChapterSteps();     // 按实际生成情况刷新章节进度（已生成/未生成）
            _syncProjectHeader();    // 刷新头栏（名称/基准日/资产类型/生成状态）
            break;
        case 'documents':
            if (currentProjectId) {
                await loadDocuments();
            }
            break;
        case 'settings':
            await loadModelSetting();
            await loadServerSettings();   // 从服务器读取全员共用的模板/材料路径
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

/** 从服务器读取"全员共用"的模板/材料路径，填进系统设置页（部署后大家看到同一套） */
async function loadServerSettings() {
    try {
        const s = await API.getServerSettings();
        const t = document.getElementById('settingTemplatePath');
        const m = document.getElementById('settingNdrcMaterialPath');
        if (t && s.template_path) { t.value = s.template_path; localStorage.setItem('reitai_settingTemplatePath', s.template_path); }
        if (m && s.materials_path) { m.value = s.materials_path; localStorage.setItem('reitai_settingNdrcMaterialPath', s.materials_path); }
    } catch (e) { /* 取不到就沿用本地 */ }
}

/** 系统设置里的路径被选中后，同时存到服务器（全员共用；映射到服务器端字段） */
function _saveServerSettingPath(inputId, value) {
    let field = null;
    if (inputId === 'settingTemplatePath') field = 'template_path';
    else if (inputId === 'settingNdrcMaterialPath') field = 'materials_path';
    if (!field) return;
    API.saveServerSettings({ [field]: value }).catch(() => {});
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
        if (!currentProjectId && projects.length > 0) {
            currentProjectId = projects[0].id;
        }

        // 取当前项目概览：名称/行业/章节进度/最近编辑时间
        let ov = null;
        try { ov = await API.getProjectOverview(); } catch (e) {
            console.warn('[REIT-AI] 项目概览加载失败:', e.message);
        }
        const hasProject = !!(ov && ov.project_name);
        const done = ov ? (ov.chapters_done || 0) : 0;
        const total = ov ? (ov.chapters_total || 7) : 7;
        const isComplete = hasProject && done >= total;

        // 项目列表这一行：显示当前正在编辑的项目
        // 名称/行业取自摘要表，状态=章节生成进度，更新时间=最近编辑时间
        const tbody = document.querySelector('#projectTable tbody');
        if (tbody) {
            if (hasProject) {
                renderProjectTable(tbody, [{
                    name: ov.project_name,
                    assetType: ov.industry || '—',
                    stage: ov.stage || '发改委申报',
                    status: isComplete ? '已完成' : `已生成 ${done}/${total} 章`,
                    statusClass: isComplete ? 'badge-success' : (done > 0 ? 'badge-warning' : 'badge-info'),
                    updateTime: ov.last_edit || '—',
                    id: 0,
                }]);
            } else {
                tbody.innerHTML = '';
            }
        }

        // 统计卡片：取自当前项目的章节进度（单项目——未全部完成即“生成中”，全部完成即“已完成”）
        const statsContainer = document.getElementById('overviewStats');
        if (statsContainer) {
            renderStatCards(statsContainer, [
                { icon: '📁', value: hasProject ? 1 : 0, label: '项目总数', color: 'blue' },
                { icon: '🔄', value: (hasProject && !isComplete) ? 1 : 0, label: '生成中', color: 'orange' },
                { icon: '✅', value: (hasProject && isComplete) ? 1 : 0, label: '已完成', color: 'green' },
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

// ===== 发改委材料生成页面功能 =====

// 本地路径选择器状态：mode='source' 是原有的"数据源文件夹"选择（限定在数据源根目录下，只能选文件夹）；
// mode='local' 是"系统设置"页用的通用选择器（不限制路径，文件夹和文件都能直接点选）。
let _picker = { mode: 'local', targetInputId: '' };

/**
 * 打开本地路径选择器（系统设置页"选择文件"按钮用）：不限制路径，文件夹可以点进去，
 * 文件可以直接点选，选中后填回对应的输入框。
 * @param {string} targetInputId - 选中的路径要填回哪个输入框的id
 */
async function openLocalPicker(targetInputId) {
    _picker = { mode: 'local', targetInputId };
    const input = document.getElementById(targetInputId);
    const startPath = input ? input.value.trim() : '';

    // 输入框里的旧值可能已经不存在、或者本身是个文件（不是文件夹）——依次尝试：
    // 原值 -> 原值的上一级目录 -> 磁盘列表，用第一个能成功打开的
    const candidates = [];
    if (startPath) {
        candidates.push(startPath);
        const idx = Math.max(startPath.lastIndexOf('\\'), startPath.lastIndexOf('/'));
        if (idx > 0) {
            candidates.push(startPath.substring(0, idx));
        }
    }
    candidates.push('');

    for (const candidate of candidates) {
        try {
            const result = await API.browseAnyPath(candidate);
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
        pathDisplay.textContent = data.current_path || '（请选择磁盘）';
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

        // 目录/文件列表
        for (const item of data.items) {
            if (item.type === 'dir') {
                html += `<div class="folder-item" onclick="navigateFolder('${item.path.replace(/\\/g, '\\\\')}')">
                    <span class="folder-icon">📁</span>
                    <span class="folder-name">${item.name}</span>
                </div>`;
            } else {
                html += `<div class="folder-item" onclick="selectLocalFile('${item.path.replace(/\\/g, '\\\\')}')">
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
        const result = await API.browseAnyPath(path);
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
        _persistSettingsPathIfNeeded(_picker.targetInputId, pathDisplay.textContent);
        _saveServerSettingPath(_picker.targetInputId, pathDisplay.textContent);
    }
    closeModal('modal-folder-browser');
    showToast('已选择路径');
}

/**
 * 选择某一个具体文件（本地任意路径模式下点文件时触发）
 */
function selectLocalFile(path) {
    const input = document.getElementById(_picker.targetInputId);
    if (input) {
        input.value = path;
        _persistSettingsPathIfNeeded(_picker.targetInputId, path);
        _saveServerSettingPath(_picker.targetInputId, path);
    }
    closeModal('modal-folder-browser');
    showToast('已选择文件');
}

/**
 * 加载章节列表
 */
async function loadChapters() {
    if (!currentProjectId) return;

    try {
        chaptersData = await API.getChapters(currentProjectId);
        renderChapterStepper(chaptersData);
        // 默认展示第一章（走 Kimi 生成 + Word 式编辑视图）
        await renderChapterEditor(1);
    } catch (error) {
        console.warn('[REIT-AI] 加载章节列表失败:', error.message);
    }
}

/**
 * 渲染章节步骤条（基于API数据）
 */
function renderChapterStepper(chapters) {
    const container = document.getElementById('chapterStepper');
    if (!container) return;

    const statusMap = {
        'extracted': 'done',
        'extracting': 'current',
        'pending': '',
    };

    // 第一项固定为"摘要表和释义"（不属于七章，chapterNum=0，走独立的点击逻辑）
    const summaryStep = {
        title: '摘要表和释义',
        status: '',
        desc: '项目基础信息',
        circle: '摘',
        chapterNum: 0,
        onClick: 'selectSummary()',
    };

    const chapterSteps = chapters.map((ch, i) => ({
        title: ch.title,
        status: statusMap[ch.status] || '',
        desc: _mapChapterStatusDesc(ch.status),
        chapterNum: i + 1,               // 真实章号（一~七 = 1~7）
        onClick: `selectChapter(${i + 1})`,
    }));

    renderStepper(container, [summaryStep, ...chapterSteps]);
}

/**
 * 映射章节状态为描述文本
 */
function _mapChapterStatusDesc(status) {
    const map = {
        'extracted': '已提取',
        'extracting': '提取中',
        'pending': '待提取',
    };
    return map[status] || '待提取';
}

/**
 * 选择章节
 * @param {number} chapterNum - 章节编号（1-7）
 */
// 已接入"Kimi 生成 + Word 式编辑"新流程的章节（1~7 全部放开）
const WIRED_CHAPTERS = new Set([1, 2, 3, 4, 5, 6, 7]);

async function selectChapter(chapterNum) {
    // 已接入新流程的章节：走 Kimi 生成 + Word 式编辑视图
    if (WIRED_CHAPTERS.has(chapterNum)) {
        await renderChapterEditor(chapterNum);
        return;
    }

    // 其余章节暂走旧的字段表单视图
    const chapterId = `chapter${chapterNum}`;
    currentChapter = chapterId;

    const titleEl = document.getElementById('chapterDetailTitle');
    if (titleEl) {
        titleEl.textContent = CHAPTER_TITLES[chapterNum] || '';
    }

    // 加载章节详情
    await loadChapterDetail(chapterId);
}

let _summaryData = null;

// 三个区块对应 _summaryData 里的键，用于 data-group 定位
const _SUMMARY_GROUPS = [
    { key: 'summary_table', title: '摘要表' },
    { key: 'glossary', title: '释义' },
    { key: 'other_info', title: '项目总体情况' },
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
        _syncProjectHeader();   // 项目名称/基准日期可能改了，刷新顶部封面栏
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

/**
 * 加载章节详情
 */
async function loadChapterDetail(chapterId) {
    if (!currentProjectId) return;

    const container = document.getElementById('chapterDetail');
    if (!container) return;

    try {
        const detail = await API.getChapterDetail(currentProjectId, chapterId);

        // 保留层级结构传递给渲染函数
        renderChapterDetail(container, {
            title: detail.title,
            status: detail.status === 'extracted' ? 'done' : (detail.status === 'extracting' ? 'current' : ''),
            sections: detail.sections || [],
        });
    } catch (error) {
        console.warn('[REIT-AI] 加载章节详情失败:', error.message);
        showToast('加载章节详情失败', 'error');
    }
}

/**
 * 预览章节
 */
function previewChapter() {
    navigate('chapter-edit');
}

// ===== 章节：Kimi 生成 + Word 式可编辑区（1~7 通用） =====

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

    // 小标题骨架来自系统设置里的官方模板；即使还没生成也能看到本章小标题
    const templatePath = localStorage.getItem('reitai_settingTemplatePath') || '';
    let content = { source: 'none', sections: [] };
    try {
        content = await API.getChapterContent(n, templatePath);
    } catch (e) { /* 后端未就绪，按空处理 */ }

    const hasSections = content.sections && content.sections.length > 0;
    const srcBadge = '';   // 章节生成状态改到顶部"申报材料章节进度"查看，编辑区标题旁不再显示
    const title = CHAPTER_TITLES[n] || '';

    // 顶部标题栏（整行）
    let html = `
        <div class="chapter-detail-header">
            <div class="flex items-center gap-12">
                <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">${title}</h3>
                ${srcBadge}
            </div>
            <div class="flex gap-8">
                <button class="btn btn-ghost btn-sm" id="btnChapterGen" onclick="runKimiChapter()">🤖 ${content.source === 'ready' ? '重新生成' : 'AI生成本章'}</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertFootnote()" title="把光标放到正文中要加脚注的位置，再点此">➕ 脚注</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertDiagram()" title="把光标放到正文中要插图的位置，再点此画框图">🖼 画图</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="openKimiChat()" title="打开 Kimi 助手：多轮对话，可粘贴文字/贴链接/上传文件，回复可一键插入正文">💬 Kimi 助手</button>
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
            html += `<div class="text-sm text-muted" style="padding:4px 0 8px 2.7rem;">以下小标题来自官方模板。点右上角"AI生成本章"自动填写，或直接在各小标题下编辑。</div>`;
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
    body.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0;">正在写入模板并生成预览…</div>';
    // 模板文件路径取自"系统设置"（localStorage）
    const templatePath = localStorage.getItem('reitai_settingTemplatePath') || '';
    try {
        const resp = await API.getChapterPreview(n, templatePath);
        if (resp.has_content) {
            body.innerHTML = resp.html;
            _previewCache[n] = resp.html;  // 缓存，供下次开关预览直接用
            if (!resp.used_template) {
                showToast('未使用模板（请在系统设置里设置模板文件路径），已用独立文档预览', 'warning');
            }
        } else {
            body.innerHTML = '<div class="text-muted text-sm" style="padding:8px 0;">暂无内容，请先生成并保存本章。</div>';
        }
    } catch (e) {
        body.innerHTML = `<div class="kimi-status error">生成预览失败：${e.message}</div>`;
    }
}

/**
 * 点击"AI生成本章"：启动当前章生成（异步），轮询，完成后重渲染编辑视图
 */
async function runKimiChapter() {
    const templatePath = localStorage.getItem('reitai_settingTemplatePath') || '';
    const materialsPath = localStorage.getItem('reitai_settingNdrcMaterialPath') || '';
    try {
        await API.runChapter(_editorChapter, templatePath, materialsPath);
    } catch (error) {
        if (!String(error.message).includes('正在生成')) {
            showToast('启动失败: ' + error.message, 'error');
            return;
        }
    }
    _pollChapterGeneration();
}

/** 轮询生成进度；完成后重渲染当前章编辑视图。生成期间把"生成"按钮变成方块"停止"。 */
function _pollChapterGeneration() {
    const n = _editorChapter;
    const btn = document.getElementById('btnChapterGen');
    const banner = document.getElementById('chapterGenBanner');
    // 生成中：按钮变成可点的方块"停止"，点一下取消
    if (btn) { btn.disabled = false; btn.innerHTML = '⏹ 停止'; btn.title = '点击停止本次生成'; btn.onclick = stopKimiChapter; }
    if (banner) { banner.className = 'kimi-status'; banner.style.display = 'block'; banner.textContent = `🤖 Kimi 正在生成${CHAPTER_TITLES[n] || '本章'}，约需数分钟，请稍候…（可点“停止”取消）`; }

    if (_kimiTimer) clearInterval(_kimiTimer);
    _kimiTimer = setInterval(async () => {
        let st;
        try { st = await API.getChapterStatus(n); } catch (e) { return; }
        if (st.status === 'done') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            delete _previewCache[n];   // 重新生成了，预览缓存作废
            await renderChapterEditor(n);   // 重渲染会把按钮恢复成“生成/重新生成”
            _syncChapterSteps();   // 该章已生成，刷新顶部进度条
            _syncProjectHeader();  // 同步头栏生成状态
            showToast('生成完成，请核对编辑');
        } else if (st.status === 'cancelled') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            if (banner) { banner.style.display = 'none'; }
            await renderChapterEditor(n);   // 恢复按钮与上次已保存内容（本次未保存半截）
            showToast('已停止生成');
        } else if (st.status === 'error') {
            clearInterval(_kimiTimer); _kimiTimer = null;
            if (btn) { btn.disabled = false; btn.innerHTML = '🤖 AI生成本章'; btn.title = ''; btn.onclick = runKimiChapter; }
            if (banner) { banner.className = 'kimi-status error'; banner.textContent = '生成失败：' + (st.error || '未知错误'); }
            showToast('生成失败', 'error');
        }
    }, 3000);
}

/** 一键停止：请求取消当前章生成（真正中止由后台在当前这一步结束后完成，轮询会收尾）。 */
async function stopKimiChapter() {
    const n = _editorChapter;
    const btn = document.getElementById('btnChapterGen');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ 停止中…'; btn.onclick = null; }
    try {
        await API.stopChapter(n);
        showToast('已请求停止，正在中止当前生成…');
    } catch (e) {
        showToast('停止失败：' + (e.message || e), 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '⏹ 停止'; btn.onclick = stopKimiChapter; }
    }
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

// ===== 💬 Kimi 助手（右侧抽屉，多轮对话）=====
let _kimiReady = false;
let _kimiDrawer = null;
let _kimiHistory = [];      // [{role:'user'|'assistant', content}]
let _kimiRange = null;      // 打开时保存的编辑区选区（用于插入回填）
let _kimiUseSel = false;    // 本次是否带上编辑区选中的文字
let _kimiSelText = '';
let _kimiWidth = null;      // 记住用户拖拽后的抽屉宽度（px），下次打开沿用
let _kimiInputH = null;     // 记住用户拖拽后的输入框高度（px）

function _kimiEsc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _ensureKimiDrawer() {
    if (_kimiReady) return;
    _kimiReady = true;
    const style = document.createElement('style');
    style.textContent = `
      .kimi-drawer{position:fixed;top:0;right:0;bottom:0;width:min(440px,94vw);background:#fff;
        border-left:1px solid #e2e6ea;box-shadow:-8px 0 24px rgba(20,30,45,.12);z-index:9999;
        display:flex;flex-direction:column;font-size:14px;}
      .kimi-drawer-head{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;
        border-bottom:1px solid #eef1f4;font-weight:600;}
      .kimi-drawer-head .khbtns button{border:none;background:transparent;cursor:pointer;font-size:13px;color:#666;margin-left:10px;}
      .kimi-sel{padding:6px 14px;font-size:12px;color:#2563a6;background:#eef4fb;border-bottom:1px solid #eef1f4;
        display:flex;align-items:center;justify-content:space-between;gap:8px;}
      .kimi-sel button{border:none;background:transparent;cursor:pointer;color:#888;}
      .kimi-msgs{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;}
      .kimi-empty{color:#999;font-size:13px;text-align:center;margin-top:28px;line-height:1.7;}
      .kimi-msg{max-width:88%;padding:8px 11px;border-radius:10px;line-height:1.55;white-space:pre-wrap;word-break:break-word;}
      .kimi-msg.user{align-self:flex-end;background:#2563a6;color:#fff;}
      .kimi-msg.assistant{align-self:flex-start;background:#f2f4f7;color:#1a2330;}
      .kimi-acts{align-self:flex-start;display:flex;gap:6px;margin:-4px 0 2px;flex-wrap:wrap;}
      .kimi-acts button{font-size:12px;border:1px solid #dfe4ea;background:#fff;border-radius:6px;padding:2px 8px;cursor:pointer;color:#333;}
      .kimi-typing{align-self:flex-start;color:#999;font-size:13px;padding:4px 2px;}
      .kimi-attach{padding:8px 14px;border-top:1px solid #eef1f4;background:#fafbfc;}
      .kimi-attach .lbl{font-size:12px;color:#666;margin:4px 0 2px;}
      .kimi-attach textarea,.kimi-attach input[type=file]{width:100%;font-size:12px;box-sizing:border-box;}
      .kimi-attach textarea{border:1px solid #dfe4ea;border-radius:6px;padding:5px;}
      .kimi-input-grip{height:9px;cursor:ns-resize;border-top:1px solid #eef1f4;display:flex;align-items:center;justify-content:center;background:#fafbfc;}
      .kimi-input-grip::before{content:'';width:34px;height:3px;border-radius:2px;background:#cdd5de;}
      .kimi-input-grip:hover::before{background:#2563a6;}
      .kimi-input-row{display:flex;align-items:flex-end;gap:8px;padding:10px 14px;}
      .kimi-input-row textarea{flex:1;resize:none;min-height:40px;max-height:none;padding:8px;border:1px solid #dfe4ea;border-radius:8px;font-size:14px;box-sizing:border-box;}
      body.kimi-vresizing{user-select:none;cursor:ns-resize;}
      .kimi-iconbtn{border:1px solid #dfe4ea;background:#fff;border-radius:8px;padding:8px 10px;cursor:pointer;
        color:#555;line-height:0;display:flex;align-items:center;justify-content:center;}
      .kimi-iconbtn:hover{background:#f2f6fb;color:#2563a6;border-color:#cdd8e3;}
      /* 左边整条都可抓着拖动改宽度；左上角露出一个圆弧手柄作为提示 */
      .kimi-resize{position:absolute;left:0;top:0;bottom:0;width:6px;cursor:col-resize;z-index:2;}
      .kimi-resize:hover{background:rgba(37,99,166,.12);}
      .kimi-grip{position:absolute;left:-9px;top:16px;width:18px;height:40px;border-radius:12px 0 0 12px;
        background:#2563a6;cursor:col-resize;z-index:3;display:flex;align-items:center;justify-content:center;
        box-shadow:-2px 0 6px rgba(20,30,45,.18);}
      .kimi-grip::before{content:'';width:6px;height:16px;
        border-left:2px solid rgba(255,255,255,.85);border-right:2px solid rgba(255,255,255,.85);}
      body.kimi-resizing{user-select:none;cursor:col-resize;}
      body.kimi-open{transition:padding-right .2s;}
      body.kimi-resizing{transition:none;}
      @media (max-width:560px){.kimi-drawer{width:100vw !important;} body.kimi-open{padding-right:0 !important;}
        .kimi-resize,.kimi-grip{display:none;}}
    `;
    document.head.appendChild(style);

    const d = document.createElement('div');
    d.className = 'kimi-drawer';
    d.style.display = 'none';
    d.innerHTML = `
      <div class="kimi-resize" id="kimiResize" title="拖动改变宽度"></div>
      <div class="kimi-grip" id="kimiGrip" title="拖动改变宽度"></div>
      <div class="kimi-drawer-head">
        <span>💬 Kimi 助手</span>
        <span class="khbtns">
          <button id="kimiNewChat" title="清空，开一个新对话">🗑 新对话</button>
          <button id="kimiClose" title="关闭">✕</button>
        </span>
      </div>
      <div class="kimi-sel" id="kimiSelChip" style="display:none;"></div>
      <div class="kimi-msgs" id="kimiMsgs"></div>
      <div class="kimi-attach" id="kimiAttach" style="display:none;">
        <div class="lbl">粘贴文字</div>
        <textarea id="kimiPasted" rows="2" placeholder="把参考资料粘贴到这里…"></textarea>
        <div class="lbl">网页链接（每行一个）</div>
        <textarea id="kimiUrls" rows="1" placeholder="https://…"></textarea>
        <div class="lbl">上传文件（Word/PPT/Excel/PDF/图片，可多选）</div>
        <input type="file" id="kimiFiles" multiple accept=".doc,.docx,.ppt,.pptx,.xls,.xlsx,.pdf,.png,.jpg,.jpeg,.txt,.md,.csv" />
        <div id="kimiFilesList" style="font-size:12px;color:#444;margin-top:4px;"></div>
      </div>
      <div class="kimi-input-grip" id="kimiInputGrip" title="拖动改变输入框高度"></div>
      <div class="kimi-input-row">
        <button class="kimi-iconbtn" id="kimiAttachBtn" title="添加素材（粘贴文字/链接/上传文件）"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg></button>
        <textarea id="kimiInput" placeholder="问 Kimi，或让它写/改一段…（Enter 发送，Shift+Enter 换行）"></textarea>
        <button class="btn btn-primary btn-sm" id="kimiSend">发送</button>
      </div>`;
    document.body.appendChild(d);
    _kimiDrawer = d;

    d.querySelector('#kimiClose').addEventListener('click', closeKimiChat);
    d.querySelector('#kimiNewChat').addEventListener('click', () => {
        _kimiHistory = []; _kimiRenderMsgs();
    });
    d.querySelector('#kimiAttachBtn').addEventListener('click', () => {
        const a = document.getElementById('kimiAttach');
        a.style.display = a.style.display === 'none' ? 'block' : 'none';
    });
    d.querySelector('#kimiFiles').addEventListener('change', (e) => {
        const names = Array.from(e.target.files || []).map(f => f.name);
        document.getElementById('kimiFilesList').textContent =
            names.length ? ('已选 ' + names.length + ' 个：' + names.join('、')) : '';
    });
    d.querySelector('#kimiSend').addEventListener('click', _kimiSend);
    d.querySelector('#kimiInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _kimiSend(); }
    });
    // 消息区的“替换/插入/复制”按钮（事件委托）
    d.querySelector('#kimiMsgs').addEventListener('click', (e) => {
        const b = e.target.closest('button[data-act]');
        if (!b) return;
        const msg = _kimiHistory[parseInt(b.dataset.i, 10)];
        if (msg) _kimiInsert(msg.content, b.dataset.act);
    });

    // 拖拽左边缘 / 左上角圆弧手柄，改变抽屉宽度（向左拖变宽，向右拖变窄）
    let dragging = false, startX = 0, startW = 0;
    const onDown = (e) => {
        dragging = true;
        startX = e.clientX;
        startW = _kimiDrawer.getBoundingClientRect().width;
        document.body.classList.add('kimi-resizing');
        e.preventDefault();
    };
    const onMove = (e) => {
        if (!dragging) return;
        let w = startW + (startX - e.clientX);          // 往左拖 clientX 变小 → 变宽
        const maxW = Math.round(window.innerWidth * 0.94);
        w = Math.max(320, Math.min(w, maxW));
        _kimiWidth = w;
        _kimiDrawer.style.width = w + 'px';
        document.body.style.paddingRight = w + 'px';
    };
    const onUp = () => {
        if (!dragging) return;
        dragging = false;
        document.body.classList.remove('kimi-resizing');
    };
    d.querySelector('#kimiResize').addEventListener('mousedown', onDown);
    d.querySelector('#kimiGrip').addEventListener('mousedown', onDown);
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);

    // 拖拽输入框顶部把手，改变输入框高度（往上拖变高、往下拖变矮）
    const ta = d.querySelector('#kimiInput');
    let vDrag = false, vStartY = 0, vStartH = 0;
    d.querySelector('#kimiInputGrip').addEventListener('mousedown', (e) => {
        vDrag = true;
        vStartY = e.clientY;
        vStartH = ta.getBoundingClientRect().height;
        document.body.classList.add('kimi-vresizing');
        e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
        if (!vDrag) return;
        let h = vStartH + (vStartY - e.clientY);          // 往上拖 clientY 变小 → 变高
        const maxH = Math.round(window.innerHeight * 0.6);
        h = Math.max(40, Math.min(h, maxH));
        _kimiInputH = h;
        ta.style.height = h + 'px';
    });
    document.addEventListener('mouseup', () => {
        if (!vDrag) return;
        vDrag = false;
        document.body.classList.remove('kimi-vresizing');
    });
}

function openKimiChat() {
    _ensureKimiDrawer();
    // 捕获编辑区选区（用于把回复插回原处），并记录选中的文字作为可选上下文
    let rng = null;
    const sel = window.getSelection();
    if (sel && sel.rangeCount) {
        let node = sel.getRangeAt(0).startContainer;
        if (node && node.nodeType === 3) node = node.parentElement;
        if (node && node.closest && node.closest('.doc-editor')) rng = sel.getRangeAt(0).cloneRange();
    }
    if (!rng && _lastEditorRange) rng = _lastEditorRange.cloneRange();
    _kimiRange = rng;
    _kimiSelText = (rng && !rng.collapsed) ? rng.toString().trim() : '';
    _kimiUseSel = !!_kimiSelText;
    const chip = document.getElementById('kimiSelChip');
    if (_kimiSelText) {
        const short = _kimiSelText.length > 40 ? _kimiSelText.slice(0, 40) + '…' : _kimiSelText;
        chip.style.display = 'flex';
        chip.innerHTML = `<span>已带上选中的 ${_kimiSelText.length} 字：${_kimiEsc(short)}</span><button id="kimiSelX" title="不带">✕</button>`;
        chip.querySelector('#kimiSelX').addEventListener('click', () => { _kimiUseSel = false; chip.style.display = 'none'; });
    } else {
        chip.style.display = 'none';
    }
    _kimiRenderMsgs();
    // 宽度：优先用用户上次拖拽后的宽度，否则默认 min(440, 94vw)
    const maxW = Math.round(window.innerWidth * 0.94);
    const w = Math.max(320, Math.min(_kimiWidth || Math.min(440, maxW), maxW));
    _kimiDrawer.style.width = w + 'px';
    _kimiDrawer.style.display = 'flex';
    document.body.classList.add('kimi-open');
    document.body.style.paddingRight = w + 'px';
    if (_kimiInputH) document.getElementById('kimiInput').style.height = _kimiInputH + 'px';
    document.getElementById('kimiInput').focus();
}

function closeKimiChat() {
    if (_kimiDrawer) _kimiDrawer.style.display = 'none';
    document.body.classList.remove('kimi-open');
    document.body.style.paddingRight = '';
}

function _kimiRenderMsgs() {
    const box = document.getElementById('kimiMsgs');
    if (!box) return;
    if (!_kimiHistory.length) {
        box.innerHTML = '<div class="kimi-empty">和 Kimi 聊聊 —— 问问题，或让它写 / 改一段申报材料。<br>点 <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg> 可粘贴文字、贴链接、上传文件作参考。</div>';
        return;
    }
    box.innerHTML = _kimiHistory.map((m, i) => {
        if (m.role === 'user') return `<div class="kimi-msg user">${_kimiEsc(m.content)}</div>`;
        return `<div class="kimi-msg assistant">${_kimiEsc(m.content)}</div>` +
            `<div class="kimi-acts">` +
            `<button data-act="replace" data-i="${i}">替换选区</button>` +
            `<button data-act="insert" data-i="${i}">插入到光标</button>` +
            `<button data-act="copy" data-i="${i}">复制</button></div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
}

async function _kimiSend() {
    const input = document.getElementById('kimiInput');
    const text = input.value.trim();
    const pasted = (document.getElementById('kimiPasted').value || '').trim();
    const urls = (document.getElementById('kimiUrls').value || '').trim();
    const fileInput = document.getElementById('kimiFiles');
    const files = (fileInput && fileInput.files) ? Array.from(fileInput.files) : [];
    const hasAttach = !!(pasted || urls || files.length);
    if (!text && !hasAttach) { showToast('请输入内容', 'warning'); return; }

    // 展示的用户气泡：用户输入 + 附件提示（素材本身不进历史，避免越滚越大）
    const shown = text + (hasAttach ? (text ? '\n' : '') + '📎 已附素材' : '');
    _kimiHistory.push({ role: 'user', content: shown });
    _kimiRenderMsgs();
    input.value = '';

    const box = document.getElementById('kimiMsgs');
    const typing = document.createElement('div');
    typing.className = 'kimi-typing'; typing.textContent = 'Kimi 正在思考…';
    box.appendChild(typing); box.scrollTop = box.scrollHeight;
    const sendBtn = document.getElementById('kimiSend');
    sendBtn.disabled = true;

    try {
        const fd = new FormData();
        // 历史只发到本轮用户消息之前（本轮消息通过 message 单独带，附件另发）
        fd.append('history', JSON.stringify(_kimiHistory.slice(0, -1)));
        fd.append('message', text);
        if (_kimiUseSel && _kimiSelText) fd.append('selected_text', _kimiSelText);
        fd.append('pasted_text', pasted);
        fd.append('urls', urls);
        files.forEach(f => fd.append('files', f, f.name));
        const r = await API.aiChat(fd);
        _kimiHistory.push({ role: 'assistant', content: r.reply || '(空回复)' });
        // 附件是一次性的，用过即清
        document.getElementById('kimiPasted').value = '';
        document.getElementById('kimiUrls').value = '';
        fileInput.value = ''; document.getElementById('kimiFilesList').textContent = '';
    } catch (e) {
        _kimiHistory.push({ role: 'assistant', content: '（出错了：' + (e.message || e) + '）' });
    } finally {
        sendBtn.disabled = false;
        _kimiRenderMsgs();
    }
}

function _kimiInsert(text, mode) {
    text = String(text || '').trim();
    if (!text) { showToast('没有可用内容', 'warning'); return; }
    if (mode === 'copy') {
        navigator.clipboard && navigator.clipboard.writeText(text);
        showToast('已复制'); return;
    }
    if (!_kimiRange) {
        showToast('请先回到正文里选中一段文字（或点一下放置光标），再打开助手', 'warning');
        return;
    }
    const range = _kimiRange;
    if (mode === 'replace' && !range.collapsed) range.deleteContents();
    else range.collapse(false);
    range.insertNode(_textToFragment(text));
    range.collapse(false);
    showToast(mode === 'replace' ? '已替换，记得点“保存”' : '已插入，记得点“保存”');
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
 * 编辑字段
 */
function editField(btn) {
    const fieldItem = btn.closest('.field-item');
    const valueEl = fieldItem.querySelector('.field-value');
    const currentValue = valueEl.textContent;

    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentValue === '（待提取）' ? '' : currentValue;
    input.className = 'form-input';
    input.style.cssText = 'padding:4px 8px;font-size:13px;';

    input.addEventListener('blur', function () {
        valueEl.textContent = this.value || currentValue;
        valueEl.style.display = '';
        this.remove();
        btn.style.display = '';
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') this.blur();
        if (e.key === 'Escape') {
            valueEl.textContent = currentValue;
            valueEl.style.display = '';
            this.remove();
            btn.style.display = '';
        }
    });

    valueEl.style.display = 'none';
    btn.style.display = 'none';
    fieldItem.insertBefore(input, btn);
    input.focus();
    input.select();
}

/**
 * 生成全部章节
 */
// ===== 一键批量生成（用 Kimi 顺序生成一~七章） =====
let _batchGenerating = false;
let _batchAbort = false;
let _batchCurrentN = 0;
let _batchTimer = null;

/** 点“全部生成”：按顺序用 Kimi 逐章生成一~七章。生成期间按钮变“停止”，可随时中止。 */
async function generateAll() {
    if (_batchGenerating) return;   // 防重复点击
    const templatePath = localStorage.getItem('reitai_settingTemplatePath') || '';
    const materialsPath = localStorage.getItem('reitai_settingNdrcMaterialPath') || '';
    if (!templatePath) {
        showToast('请先到「系统设置」设置模板文件路径，再生成', 'warning');
        return;
    }
    _batchGenerating = true;
    _batchAbort = false;
    _setBatchButton(true);
    _setOverallProgress(0);

    let doneCount = 0;
    for (let n = 1; n <= 7; n++) {
        if (_batchAbort) break;
        _batchCurrentN = n;
        _setBatchStatus(`正在生成 ${CHAPTER_TITLES[n] || ('第' + n + '章')}…（约数分钟）`);
        _setChapterStep(n, 'current');
        // 启动本章
        try {
            await API.runChapter(n, templatePath, materialsPath);
        } catch (e) {
            if (!String(e.message).includes('正在生成')) {
                showToast(`第${n}章启动失败：${e.message}`, 'error');
                _setChapterStep(n, '');
                break;
            }
            // “正在生成”说明后台已在跑，继续轮询即可
        }
        // 等本章结束
        const result = await _waitChapterDone(n);
        if (result === 'cancelled' || _batchAbort) { _setChapterStep(n, ''); break; }
        if (result === 'error') {
            showToast(`第${n}章生成失败，已停止批量生成`, 'error');
            _setChapterStep(n, '');
            break;
        }
        doneCount = n;
        _setChapterStep(n, 'done');
        _setOverallProgress(Math.round(n / 7 * 100));
    }

    _batchGenerating = false;
    _batchCurrentN = 0;
    _setBatchButton(false);
    _syncChapterSteps();     // 收尾：按实际生成情况刷新章节进度
    _syncProjectHeader();    // 收尾：刷新头栏生成状态
    // 各章内容已更新，作废预览缓存
    try { if (typeof _previewCache === 'object') { for (const k in _previewCache) delete _previewCache[k]; } } catch (e) { }

    if (_batchAbort) {
        _setBatchStatus('已停止');
        showToast('已停止批量生成');
    } else if (doneCount === 7) {
        _setBatchStatus('全部完成');
        showToast('一~七章已全部生成完成！请逐章核对');
    } else {
        _setBatchStatus('');
    }
}

/** 等某章生成结束，resolve 为 'done' | 'error' | 'cancelled' */
function _waitChapterDone(n) {
    return new Promise(resolve => {
        if (_batchTimer) clearInterval(_batchTimer);
        _batchTimer = setInterval(async () => {
            let st;
            try { st = await API.getChapterStatus(n); } catch (e) { return; }
            if (st.status === 'done' || st.status === 'error' || st.status === 'cancelled') {
                clearInterval(_batchTimer); _batchTimer = null;
                resolve(st.status);
            }
        }, 3000);
    });
}

/** “全部生成” <-> “停止生成” 按钮切换 */
function _setBatchButton(running) {
    const btn = document.getElementById('btnGenerateAll');
    if (!btn) return;
    btn.disabled = false;
    if (running) {
        btn.innerHTML = '⏹ 停止生成';
        btn.onclick = stopBatchGenerate;
    } else {
        btn.innerHTML = '🤖 全部生成';
        btn.onclick = generateAll;
    }
}

/** 停止批量：中止当前正在生成的这一章，并终止后续 */
async function stopBatchGenerate() {
    _batchAbort = true;
    const btn = document.getElementById('btnGenerateAll');
    if (btn) { btn.disabled = true; btn.innerHTML = '⏳ 停止中…'; }
    _setBatchStatus('正在停止…');
    try {
        if (_batchCurrentN) await API.stopChapter(_batchCurrentN);
    } catch (e) { /* 忽略 */ }
    if (btn) btn.disabled = false;
}

function _setOverallProgress(pct) {
    const fill = document.getElementById('progressFill');
    const label = document.getElementById('progressPercent');
    if (fill) fill.style.width = pct + '%';
    if (label) label.textContent = pct + '%';
}

function _setBatchStatus(text) {
    const el = document.getElementById('batchStatus');
    if (el) el.textContent = text || '';
}

/** 更新顶部步骤条某章状态：'current' | 'done' | '' */
function _setChapterStep(n, state) {
    const step = document.querySelector('.stepper .step[data-chapter="' + n + '"]');
    if (!step) return;
    step.classList.remove('done', 'current');
    const circle = step.querySelector('.step-circle');
    const desc = step.querySelector('.step-desc');
    if (state === 'done') {
        step.classList.add('done');
        if (circle) circle.textContent = '✓';
        if (desc) desc.textContent = '已生成';
    } else if (state === 'current') {
        step.classList.add('current');
        if (circle) circle.textContent = n;
        if (desc) desc.textContent = '生成中…';
    } else {
        if (circle) circle.textContent = n;
        if (desc) desc.textContent = '未生成';
    }
}

/** 按实际情况刷新顶部步骤条：
 *  - 摘要表和释义（data-chapter="0"）：随摘要表是否已填 → 已填写/未填写；
 *  - 正文一~七（data-chapter=1~7）：已产出 ch{n}.json → 已生成，否则未生成。 */
async function _syncChapterSteps() {
    let ov;
    try { ov = await API.getProjectOverview(); } catch (e) { return; }
    const done = (ov && Array.isArray(ov.done_chapters)) ? ov.done_chapters : [];

    // 摘要表和释义节点（独立一节，圆圈保持"摘"）
    const sumStep = document.querySelector('.stepper .step[data-chapter="0"]');
    if (sumStep) {
        const filled = !!(ov && ov.summary_filled);
        sumStep.classList.remove('done', 'current');
        const d = sumStep.querySelector('.step-desc');
        const c = sumStep.querySelector('.step-circle');
        if (filled) { sumStep.classList.add('done'); }
        if (d) d.textContent = filled ? '已填写' : '未填写';
        if (c) c.textContent = '摘';
    }

    // 正文七章
    for (let n = 1; n <= 7; n++) {
        _setChapterStep(n, done.includes(n) ? 'done' : '');
    }
}
document.addEventListener('DOMContentLoaded', _syncChapterSteps);

/**
 * 开始轮询生成进度
 */
function startProgressPolling() {
    if (generationPollingTimer) {
        clearInterval(generationPollingTimer);
    }

    generationPollingTimer = setInterval(async () => {
        try {
            const status = await API.getGenerateStatus(currentProjectId);
            updateProgressUI(status);

            if (status.status === 'completed' || status.status === 'error') {
                clearInterval(generationPollingTimer);
                generationPollingTimer = null;

                if (status.status === 'completed') {
                    showToast('文档生成完成！');
                } else {
                    showToast('文档生成失败: ' + (status.message || '未知错误'), 'error');
                }
            }
        } catch (error) {
            console.warn('[REIT-AI] 进度查询失败:', error.message);
        }
    }, 2000);
}

/**
 * 更新进度UI
 */
function updateProgressUI(status) {
    const fill = document.getElementById('progressFill');
    const label = document.getElementById('progressPercent');

    if (fill && status.progress_percent !== undefined) {
        fill.style.width = status.progress_percent + '%';
    }
    if (label && status.progress_percent !== undefined) {
        label.textContent = status.progress_percent + '%';
    }
}

// ===== 章节编辑页面功能 =====

function resetChapterEdit() {
    showToast('已重置编辑内容', 'warning');
    // 重新加载章节数据
    if (currentProjectId && currentChapter) {
        loadChapterDetail(currentChapter);
    }
}

async function saveChapterEdit() {
    if (!currentProjectId || !currentChapter) {
        showToast('无法保存：未选择项目或章节', 'error');
        return;
    }

    // 收集编辑器表单中的数据
    const formBody = document.getElementById('editorForm');
    if (!formBody) return;

    const inputs = formBody.querySelectorAll('input, textarea');
    const fields = {};
    inputs.forEach((input, index) => {
        const label = input.closest('.form-group')?.querySelector('label')?.textContent || `field_${index}`;
        fields[label] = input.value;
    });

    try {
        await API.updateChapterData(currentProjectId, currentChapter, fields);
        showToast('章节内容已保存');
    } catch (error) {
        showToast('保存失败: ' + error.message, 'error');
    }
}

// ===== 文档管理页面功能 =====

/**
 * 加载文档列表
 */
async function loadDocuments() {
    if (!currentProjectId) return;

    try {
        const result = await API.getDocuments(currentProjectId);
        const tbody = document.querySelector('#docTable tbody');
        if (tbody && result.documents) {
            if (result.documents.length > 0) {
                renderDocTable(tbody, result.documents.map(doc => ({
                    id: doc.filename,
                    name: doc.filename,
                    type: 'docx',
                    time: doc.created_at || '-',
                    size: doc.size_formatted || '-',
                    status: '已完成',
                })));
            }
        }
    } catch (error) {
        console.warn('[REIT-AI] 加载文档列表失败:', error.message);
    }
}

function switchDocTab(tab) {
    document.querySelectorAll('#docTabBar .tab-item').forEach(item => {
        item.classList.remove('active');
    });
    event.target.classList.add('active');
    // 根据tab过滤（当前简化处理，后续可扩展）
    if (currentProjectId) {
        loadDocuments();
    }
}

function downloadDoc(docId) {
    if (!currentProjectId) {
        showToast('未选择项目', 'warning');
        return;
    }
    API.downloadDocument(currentProjectId);
    showToast('开始下载文档...');
}

function regenerateDoc(docId) {
    if (!currentProjectId) {
        showToast('未选择项目', 'warning');
        return;
    }
    generateAll();
}

function deleteDoc(docId) {
    if (confirm('确定要删除此文档吗？')) {
        showToast('文档删除功能暂不支持', 'warning');
    }
}

// ===== 项目创建 =====

/**
 * 选择项目并进入发改委材料生成页
 * @param {number} projectId - 项目ID
 */
function selectProject(projectId) {
    currentProjectId = projectId;
    navigate('ndrc');
}

/**
 * 初始化应用
 */
async function initApp() {
    console.log('[REIT-AI] 系统初始化中...');

    _initAuthUI();   // 若启用了登录，顶栏显示当前用户 + 退出按钮

    // 恢复系统设置页里保存过的路径
    restoreSettingsPaths();

    // 检查后端健康状态
    try {
        const health = await API.get('/health');
        console.log('[REIT-AI] 后端服务连接正常:', health);

        // 自动加载项目列表
        const projects = await API.getProjects();
        if (projects && projects.length > 0) {
            currentProjectId = projects[0].id;
            console.log('[REIT-AI] 已自动选择项目:', projects[0].name, 'ID=', currentProjectId);
        }
    } catch (error) {
        console.warn('[REIT-AI] 后端服务未就绪:', error.message);
    }

    // 默认显示概览页面
    navigate('overview');

    // 绑定全局事件
    bindGlobalEvents();

    console.log('[REIT-AI] 系统初始化完成');
}

/**
 * 绑定全局事件监听器
 */
function bindGlobalEvents() {
    // 点击弹窗外部关闭
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', function (e) {
            if (e.target === this) {
                this.classList.remove('show');
            }
        });
    });

    // ESC 关闭弹窗
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-overlay.show').forEach(modal => {
                modal.classList.remove('show');
            });
        }
    });
}

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', initApp);


/* ===================== 封面编辑器 ===================== */
let _coverReady = false;
let _coverState = null;
let _coverBust = 0;
let _coverUploadRole = null;

// 4 个 logo 角色（顺序 = 封面从上到下 / 底部从左到右）
const _COVER_ROLES = [
    { key: 'issuer', label: '发行人', hint: '（原始权益人，如奥飞数据）', pos: '标题下方' },
    { key: 'fund_manager', label: '基金管理人', hint: '', pos: '底部·左' },
    { key: 'plan_manager', label: '专项计划管理人', hint: '（即资产支持证券管理人）', pos: '底部·中' },
    { key: 'advisor', label: '财务顾问', hint: '', pos: '底部·右' },
];

function _coverEsc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _ensureCoverModal() {
    if (_coverReady) return;
    _coverReady = true;
    const style = document.createElement('style');
    style.textContent = `
      .cover-overlay{position:fixed;inset:0;background:rgba(20,30,45,.45);z-index:10000;display:none;align-items:center;justify-content:center;padding:24px;}
      .cover-overlay.show{display:flex;}
      .cover-modal{background:#fff;border-radius:12px;width:min(1000px,96vw);max-height:92vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(20,30,45,.3);}
      .cover-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #eef1f4;}
      .cover-head .t{font-weight:600;font-size:15px;}
      .cover-head .x{border:none;background:transparent;font-size:18px;cursor:pointer;color:#888;}
      .cover-body{display:flex;flex:1;min-height:0;}
      .cover-form{width:42%;overflow-y:auto;padding:16px 18px;border-right:1px solid #eef1f4;}
      .cover-prev{flex:1;overflow-y:auto;padding:20px;background:#f6f7f9;}
      .cover-sec{font-size:12px;font-weight:600;color:#555;margin:16px 0 6px;}
      .cover-sec:first-child{margin-top:0;}
      .cover-ro{background:#fbecec;border:1px solid #f2caca;border-radius:6px;padding:8px 10px;font-size:13px;color:#333;line-height:1.7;}
      .cover-ro .tag{display:inline-block;font-size:11px;color:#c0392b;background:#fff;border:1px solid #f2caca;border-radius:4px;padding:0 5px;margin-right:6px;}
      .cover-logo-row{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px dashed #eef1f4;}
      .cover-logo-thumb{width:76px;height:48px;border:1px solid #e2e6ea;border-radius:6px;display:flex;align-items:center;justify-content:center;background:#fffdf5;overflow:hidden;flex:none;}
      .cover-logo-thumb img{max-width:100%;max-height:100%;}
      .cover-logo-thumb .ph{font-size:11px;color:#c9b976;}
      .cover-logo-meta{flex:1;min-width:0;}
      .cover-logo-meta .nm{font-size:13px;font-weight:600;color:#222;}
      .cover-logo-meta .ht{font-size:11px;color:#999;}
      .cover-logo-acts button{font-size:12px;border:1px solid #dfe4ea;background:#fff;border-radius:6px;padding:3px 9px;cursor:pointer;margin-left:4px;}
      .cover-logo-acts button:hover{background:#f2f6fb;border-color:#cdd8e3;}
      .cover-date-in{width:100%;box-sizing:border-box;border:1.5px solid #333;border-radius:6px;padding:8px 10px;font-size:14px;}
      .cover-foot{display:flex;justify-content:flex-end;gap:8px;padding:12px 18px;border-top:1px solid #eef1f4;}
      /* 预览：仿封面版式 */
      .cv-page{background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.1);max-width:520px;margin:0 auto;padding:50px 40px;min-height:660px;}
      .cv-title{text-align:center;font-weight:700;font-size:19px;line-height:2;color:#111;}
      .cv-issuer{text-align:center;margin:26px 0 12px;min-height:40px;}
      .cv-issuer img{max-width:230px;max-height:96px;}
      .cv-oo-label{text-align:center;font-size:14px;color:#333;margin-top:6px;}
      .cv-oo{text-align:center;font-size:14px;color:#333;line-height:2.1;}
      .cv-date{text-align:center;font-size:14px;margin:18px 0;color:#333;min-height:20px;}
      .cv-bottom{display:flex;justify-content:center;gap:18px;align-items:center;margin-top:44px;flex-wrap:wrap;}
      .cv-bottom .slot{max-width:150px;text-align:center;}
      .cv-bottom img{max-width:150px;max-height:60px;}
      .cv-miss{color:#c9ccd1;font-size:12px;border:1px dashed #d7dbe0;border-radius:4px;padding:14px 10px;}
    `;
    document.head.appendChild(style);

    const ov = document.createElement('div');
    ov.className = 'cover-overlay';
    ov.id = 'coverOverlay';
    ov.innerHTML = `
      <div class="cover-modal">
        <div class="cover-head">
          <span class="t">🖼 编辑封面</span>
          <button class="x" id="coverClose" title="关闭">✕</button>
        </div>
        <div class="cover-body">
          <div class="cover-form" id="coverForm"></div>
          <div class="cover-prev"><div class="cv-page" id="coverPreview"></div></div>
        </div>
        <div class="cover-foot">
          <button class="btn btn-ghost btn-sm" id="coverDownload">⬇ 下载封面Word</button>
          <button class="btn btn-primary btn-sm" id="coverSave">💾 保存</button>
        </div>
      </div>`;
    document.body.appendChild(ov);

    ov.querySelector('#coverClose').addEventListener('click', closeCoverEditor);
    ov.addEventListener('click', (e) => { if (e.target === ov) closeCoverEditor(); });
    ov.querySelector('#coverSave').addEventListener('click', _coverSave);
    ov.querySelector('#coverDownload').addEventListener('click', _coverDownload);
}

async function openCoverEditor() {
    _ensureCoverModal();
    document.getElementById('coverOverlay').classList.add('show');
    await _coverLoad();
}

function closeCoverEditor() {
    const ov = document.getElementById('coverOverlay');
    if (ov) ov.classList.remove('show');
}

async function _coverLoad() {
    const form = document.getElementById('coverForm');
    const prev = document.getElementById('coverPreview');
    form.innerHTML = '<div style="color:#999;font-size:13px">加载中…</div>';
    prev.innerHTML = '';
    try {
        _coverState = await API.getCover();
    } catch (e) {
        form.innerHTML = '<div style="color:#c0392b;font-size:13px">加载失败：' + _coverEsc(e.message) + '</div>';
        return;
    }
    _coverBust = Date.now();   // 每次加载刷新图片缓存戳
    _coverRenderForm();
    _coverRenderPreview();
}

function _coverRenderForm() {
    const s = _coverState;
    const form = document.getElementById('coverForm');
    const oo = (s.originators || []).map(x => _coverEsc(x)).join('<br>')
        || '<span style="color:#999">（摘要表未填「原始权益人」）</span>';
    let logoRows = '';
    _COVER_ROLES.forEach(r => {
        const has = s.logos[r.key] && s.logos[r.key].has;
        const thumb = has ? `<img src="${API.coverLogoUrl(r.key, _coverBust)}">` : '<span class="ph">未上传</span>';
        logoRows += `
          <div class="cover-logo-row">
            <div class="cover-logo-thumb">${thumb}</div>
            <div class="cover-logo-meta">
              <div class="nm">${_coverEsc(r.label)} <span class="ht">${_coverEsc(r.hint)}</span></div>
              <div class="ht">封面位置：${_coverEsc(r.pos)}</div>
            </div>
            <div class="cover-logo-acts">
              <button data-up="${r.key}">${has ? '替换' : '上传'}</button>
              ${has ? `<button data-del="${r.key}">删除</button>` : ''}
            </div>
          </div>`;
    });
    const titleHtml = (s.title_lines || []).filter(Boolean).map(x => _coverEsc(x)).join('<br>')
        || '<span style="color:#999">（摘要表未填「项目名称」）</span>';
    form.innerHTML = `
      <div class="cover-sec">🔴 标题（自动取自摘要表·不可编辑）</div>
      <div class="cover-ro"><span class="tag">只读</span>${titleHtml}</div>
      <div class="cover-sec">🔴 原始权益人（自动取自摘要表·不可编辑）</div>
      <div class="cover-ro"><span class="tag">只读</span>${oo}</div>
      <div class="cover-sec">🟡 Logo 图片（上传 PNG / JPG）</div>
      ${logoRows}
      <div class="cover-sec">⬛ 日期（请填写，如「2026 年 7 月」）</div>
      <input type="text" class="cover-date-in" id="coverDateIn" placeholder="2026 年 7 月" value="${_coverEsc(s.date_text || '')}">
      <input type="file" id="coverFileInput" accept=".png,.jpg,.jpeg" style="display:none">
    `;
    form.querySelector('#coverDateIn').addEventListener('input', (e) => {
        _coverState.date_text = e.target.value;
        _coverRenderPreview();
    });
    const fileIn = form.querySelector('#coverFileInput');
    form.querySelectorAll('button[data-up]').forEach(b => {
        b.addEventListener('click', () => { _coverUploadRole = b.dataset.up; fileIn.value = ''; fileIn.click(); });
    });
    form.querySelectorAll('button[data-del]').forEach(b => {
        b.addEventListener('click', () => _coverDeleteLogo(b.dataset.del));
    });
    fileIn.addEventListener('change', _coverFilePicked);
}

async function _coverFilePicked(e) {
    const file = e.target.files && e.target.files[0];
    if (!file || !_coverUploadRole) return;
    try {
        showToast('上传中…');
        await API.uploadCoverLogo(_coverUploadRole, file);
        await _coverLoad();
        showToast('已上传');
    } catch (err) {
        showToast('上传失败：' + err.message, 'error');
    }
}

async function _coverDeleteLogo(role) {
    try {
        await API.deleteCoverLogo(role);
        await _coverLoad();
        showToast('已删除');
    } catch (err) {
        showToast('删除失败：' + err.message, 'error');
    }
}

function _coverRenderPreview() {
    const s = _coverState;
    const prev = document.getElementById('coverPreview');
    if (!s) return;
    const titleHtml = (s.title_lines || []).filter(Boolean).map(x => _coverEsc(x)).join('<br>');
    const issuerHas = s.logos.issuer && s.logos.issuer.has;
    const issuer = issuerHas
        ? `<img src="${API.coverLogoUrl('issuer', _coverBust)}">`
        : '<div class="cv-miss">发行人 logo（待上传）</div>';
    const ooLines = (s.originators || []).map(x => `<div>${_coverEsc(x)}</div>`).join('');
    let bottom = '';
    ['fund_manager', 'plan_manager', 'advisor'].forEach(k => {
        const has = s.logos[k] && s.logos[k].has;
        bottom += `<div class="slot">${has
            ? `<img src="${API.coverLogoUrl(k, _coverBust)}">`
            : `<div class="cv-miss">${_coverEsc(s.logos[k].label)}<br>（待上传）</div>`}</div>`;
    });
    prev.innerHTML = `
      <div class="cv-title">${titleHtml}</div>
      <div class="cv-issuer">${issuer}</div>
      <div class="cv-oo-label">原始权益人</div>
      <div class="cv-oo">${ooLines}</div>
      <div class="cv-date">${_coverEsc(s.date_text || '')}</div>
      <div class="cv-bottom">${bottom}</div>
    `;
}

async function _coverSave() {
    try {
        await API.saveCoverDate((_coverState && _coverState.date_text) || '');
        showToast('已保存');
    } catch (err) {
        showToast('保存失败：' + err.message, 'error');
    }
}

async function _coverDownload() {
    try { await API.saveCoverDate((_coverState && _coverState.date_text) || ''); } catch (e) { }
    API.downloadCover();
}

// 让顶部"封面栏"的项目名称/基准日期跟随摘要表，而不是写死的演示文字
async function _syncProjectHeader() {
    const info = document.querySelector('#page-ndrc .ph-info');
    if (!info) return;
    // 名称 / 基准日期 / 资产类型 ← 摘要表
    try {
        const s = await API.getCover();
        const h2 = info.querySelector('h2');
        if (h2 && s.project_name) h2.textContent = s.project_name;
        const p = info.querySelector('p');
        if (p) p.textContent = '基准日期：' + (s.base_date || '—') + ' | 发改委申报阶段';
        const tag = info.querySelector('.ph-tags .badge-info');
        if (tag && s.industry) tag.textContent = s.industry;
    } catch (e) { /* 保持原样 */ }
    // 生成状态标签 ← 章节进度（未开始 / 生成中 X/总 / 已完成）
    try {
        const ov = await API.getProjectOverview();
        const st = document.getElementById('phStatusBadge');
        if (st && ov) {
            const done = ov.chapters_done || 0;
            const total = ov.chapters_total || 7;
            if (done >= total) { st.className = 'badge badge-success'; st.textContent = '已完成'; }
            else if (done > 0) { st.className = 'badge badge-warning'; st.textContent = `生成中 ${done}/${total}`; }
            else { st.className = 'badge badge-info'; st.textContent = '未开始'; }
        }
    } catch (e) { /* 保持原样 */ }
}
document.addEventListener('DOMContentLoaded', _syncProjectHeader);


/* ===================== 登录 / 退出 ===================== */
async function _initAuthUI() {
    const box = document.getElementById('authBox');
    if (!box) return;
    try {
        const resp = await fetch(API_BASE + '/auth/me');   // 不走 API.request，避免未启用登录时误跳转
        if (resp.ok) {
            const d = await resp.json();
            box.innerHTML = `<span class="text-sm text-muted" style="margin-right:8px">👤 ${_escHtmlAttr(d.username || '')}</span>` +
                `<button class="btn btn-ghost btn-sm" onclick="logout()">退出</button>`;
        } else {
            box.innerHTML = '';   // 未启用登录（或未登录）——不显示
        }
    } catch (e) {
        box.innerHTML = '';
    }
}

async function logout() {
    try { await fetch(API_BASE + '/auth/logout', { method: 'POST' }); } catch (e) { /* 忽略 */ }
    window.location.href = '/login';
}


/* ===================== 新建项目 ===================== */
/** 拉取可用模型填进某个 <select>，并选中当前所用模型 */
async function _fillModelSelect(selectId) {
    const sel = document.getElementById(selectId);
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

// 对话框模式：'create'（新建）| 'edit'（编辑现有项目）
let _projectDialogMode = 'create';

function _applyProjectDialogMode(mode) {
    _projectDialogMode = mode;
    const title = document.getElementById('npDialogTitle');
    const btn = document.getElementById('npSubmitBtn');
    if (mode === 'edit') {
        if (title) title.textContent = '✏️ 编辑项目';
        if (btn) btn.textContent = '保 存 修 改';
    } else {
        if (title) title.textContent = '➕ 新建项目';
        if (btn) btn.textContent = '创 建 项 目';
    }
}

function openNewProject() {
    _applyProjectDialogMode('create');
    const nameEl = document.getElementById('npName');
    const tEl = document.getElementById('npTemplatePath');
    const mEl = document.getElementById('npMaterialsPath');
    if (nameEl) nameEl.value = '';
    // 预填已保存的路径，方便复用（可再改）
    if (tEl) tEl.value = localStorage.getItem('reitai_settingTemplatePath') || '';
    if (mEl) mEl.value = localStorage.getItem('reitai_settingNdrcMaterialPath') || '';
    _fillModelSelect('npModel');   // 加载模型下拉，默认选中当前所用模型
    openModal('modal-new-project');
    if (nameEl) setTimeout(() => nameEl.focus(), 50);
}

/** 编辑现有项目：与"新建项目"同一对话框，预填当前所选的名称/文件夹/模板/模型，可再改 */
async function openEditProject() {
    _applyProjectDialogMode('edit');
    const nameEl = document.getElementById('npName');
    const tEl = document.getElementById('npTemplatePath');
    const mEl = document.getElementById('npMaterialsPath');
    if (tEl) tEl.value = localStorage.getItem('reitai_settingTemplatePath') || '';
    if (mEl) mEl.value = localStorage.getItem('reitai_settingNdrcMaterialPath') || '';
    if (nameEl) nameEl.value = '';
    _fillModelSelect('npModel');   // 默认选中当前正在用的模型
    openModal('modal-new-project');
    // 预填当前项目名称（来自摘要表）
    try {
        const ov = await API.getProjectOverview();
        if (nameEl && ov && ov.project_name) nameEl.value = ov.project_name;
    } catch (e) { /* 取不到就留空 */ }
}

async function submitNewProject() {
    const name = (document.getElementById('npName')?.value || '').trim();
    const materials = (document.getElementById('npMaterialsPath')?.value || '').trim();
    const template = (document.getElementById('npTemplatePath')?.value || '').trim();
    if (!name) { showToast('请填写项目名称', 'warning'); return; }
    if (!materials) { showToast('请选择申报材料文件夹', 'warning'); return; }
    if (!template) { showToast('请选择申报材料格式文本（模板文件）', 'warning'); return; }

    // 路径存进与「系统设置」同一套 key，供后续各章生成/预览直接使用，并同步设置页输入框
    localStorage.setItem('reitai_settingTemplatePath', template);
    localStorage.setItem('reitai_settingNdrcMaterialPath', materials);
    const st = document.getElementById('settingTemplatePath'); if (st) st.value = template;
    const sm = document.getElementById('settingNdrcMaterialPath'); if (sm) sm.value = materials;
    // 同时存到服务器（全员共用）
    try { await API.saveServerSettings({ template_path: template, materials_path: materials }); } catch (e) { /* 不阻塞 */ }

    // 应用所选模型（全局即时生效，各章生成都用它），并同步设置页下拉
    const model = (document.getElementById('npModel')?.value || '').trim();
    if (model) {
        try { await API.setModel(model); } catch (e) { showToast('切换模型失败：' + e.message, 'error'); }
        const sModel = document.getElementById('settingModel'); if (sModel) sModel.value = model;
    }

    // 保存项目组自定义显示名（列表展示用，可编辑；两种模式都保存）
    try { await API.saveProjectName(name); } catch (e) { /* 不阻塞 */ }

    // 编辑模式：只更新配置（名称/路径/模型），不新建项目
    if (_projectDialogMode === 'edit') {
        showToast('修改已保存');
        closeModal('modal-new-project');
        try { await loadOverviewData(); } catch (e) { /* 刷新失败不阻塞 */ }
        _syncProjectHeader();
        return;
    }

    // 新建模式：创建项目并进入材料生成页
    try {
        const proj = await API.createProject(name, materials);
        if (proj && (proj.id || proj.project_id)) currentProjectId = proj.id || proj.project_id;
        showToast('项目已创建');
        closeModal('modal-new-project');
        try { await loadOverviewData(); } catch (e) { /* 列表刷新失败不阻塞 */ }
        navigate('ndrc');
    } catch (e) {
        showToast('创建失败：' + e.message, 'error');
    }
}
