/**
 * REIT-AI 法律文件生成系统 - 增强功能模块
 * 包含：释义表、承诺函、财务数据、不涉及标记、基准日配置、附件管理
 */

// ===== 增强功能 API 调用封装 =====

// 后端统一返回 {success, data} 包裹，这里解出 data 供各面板直接使用
function _unwrapEnhanceResp(resp) {
    return (resp && resp.data !== undefined) ? resp.data : (resp || {});
}

const EnhancementsAPI = {
    // --- 释义表 ---（后端仅提供整体读取/整体保存两个路由）
    async getGlossary(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/glossary`));
    },
    async updateGlossary(projectId, entries) {
        return API.put(`/projects/${projectId}/glossary`, { entries });
    },

    // --- 承诺函 ---
    async getCommitments(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/commitments`));
    },
    async fillCommitment(projectId, templateId, variables) {
        return API.post(`/projects/${projectId}/commitments/fill`, { template_id: templateId, variables });
    },

    // --- 财务数据 ---（后端路由为 /financial-data）
    async getFinancialData(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/financial-data`));
    },
    async saveFinancialData(projectId, data) {
        return API.put(`/projects/${projectId}/financial-data`, { data });
    },

    // --- 不涉及模块 ---（后端请求体为 {sections, reason}）
    async getInapplicable(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/inapplicable`));
    },
    async updateInapplicable(projectId, sectionIds, reason) {
        return API.put(`/projects/${projectId}/inapplicable`, { sections: sectionIds, reason });
    },

    // --- 基准日 ---（后端请求体为 {base_date, query_point, extra}）
    async getQueryDates(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/query-dates`));
    },
    async updateQueryDates(projectId, dates) {
        // 评估基准日 → base_date；申报日期 → query_point；其余日期收入 extra 原样保存
        const { evaluation_date = '', report_date = '', ...extra } = dates || {};
        return API.put(`/projects/${projectId}/query-dates`, {
            base_date: evaluation_date,
            query_point: report_date,
            extra,
        });
    },

    // --- 附件 ---（后端条目格式为 {id, title, filename}，前端展示用 {id, name, type}，此处互转）
    async getAttachments(projectId) {
        return _unwrapEnhanceResp(await API.get(`/projects/${projectId}/attachments`));
    },
    async updateAttachments(projectId, attachments) {
        const items = (attachments || []).map(a => ({ id: a.id, title: a.name, filename: a.type || '' }));
        return API.put(`/projects/${projectId}/attachments`, { attachments: items });
    },
};

// ===== 增强功能状态管理 =====
const EnhancementsState = {
    activeTab: 'glossary',
    glossary: { entries: [], searchText: '' },
    commitments: { templates: [], selectedId: null },
    financial: { rows: [], columns: [] },
    inapplicable: { sections: [] },
    queryDates: { dates: {} },
    attachments: { items: [] },
    loading: false,
};

// ===== 增强功能面板控制 =====
function togglePanel(panelId) {
    const body = document.getElementById(`${panelId}-body`);
    const icon = body.previousElementSibling.querySelector('.toggle-icon');
    if (body.style.display === 'none') {
        body.style.display = 'block';
        icon.textContent = '▲';
        body.style.maxHeight = '0';
        body.offsetHeight; // force reflow
        body.style.maxHeight = body.scrollHeight + 'px';
        setTimeout(() => { body.style.maxHeight = 'none'; }, 350);
        // Load active tab data
        switchEnhancementTab(EnhancementsState.activeTab);
    } else {
        body.style.maxHeight = body.scrollHeight + 'px';
        body.offsetHeight;
        body.style.maxHeight = '0';
        icon.textContent = '▼';
        setTimeout(() => { body.style.display = 'none'; body.style.maxHeight = ''; }, 350);
    }
}

function switchEnhancementTab(tabName) {
    EnhancementsState.activeTab = tabName;
    // Update tab buttons
    document.querySelectorAll('.enhancement-tabs .tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    // Render content
    const container = document.getElementById('enhancement-content');
    if (!container) return;
    switch (tabName) {
        case 'glossary': renderGlossaryPanel('enhancement-content'); break;
        case 'commitment': renderCommitmentSelector('enhancement-content'); break;
        case 'financial': renderFinancialInput('enhancement-content'); break;
        case 'inapplicable': renderInapplicableMarker('enhancement-content'); break;
        case 'querydate': renderQueryDateConfig('enhancement-content'); break;
        case 'attachment': renderAttachmentManager('enhancement-content'); break;
    }
}

// ===== 通用工具函数 =====
function enhShowLoading(containerId) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = '<div class="enhancement-loading"><div class="enhancement-spinner"></div><span>加载中...</span></div>';
}

function enhShowError(containerId, msg) {
    const el = document.getElementById(containerId);
    if (el) el.innerHTML = `<div class="enhancement-error"><span>⚠️ ${msg}</span><button class="btn btn-ghost btn-sm" onclick="switchEnhancementTab('${EnhancementsState.activeTab}')">重试</button></div>`;
}

function enhShowToast(message, type = 'success') {
    const toast = document.getElementById('globalToast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast show' + (type === 'error' ? ' error' : type === 'warning' ? ' warning' : '');
    setTimeout(() => { toast.className = 'toast'; }, 3000);
}

function formatNumber(num) {
    if (num === null || num === undefined || num === '') return '';
    const n = parseFloat(num);
    if (isNaN(n)) return num;
    return n.toLocaleString('zh-CN');
}

function parseFormattedNumber(str) {
    if (!str) return 0;
    return parseFloat(str.replace(/,/g, '')) || 0;
}

// ===== 1. 释义表面板 =====
async function renderGlossaryPanel(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Render structure first
    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-toolbar">
                <input type="text" class="form-input enhancement-search" 
                    placeholder="搜索术语..." 
                    oninput="filterGlossary(this.value)"
                    value="${EnhancementsState.glossary.searchText}">
                <button class="btn btn-primary btn-sm" onclick="addGlossaryEntry()">+ 新增术语</button>
            </div>
            <div class="enhancement-table-wrap">
                <table class="data-table enhancement-glossary-table">
                    <thead>
                        <tr>
                            <th style="width:30%">术语/简称</th>
                            <th style="width:50%">全称/释义</th>
                            <th style="width:20%">操作</th>
                        </tr>
                    </thead>
                    <tbody id="glossaryTableBody">
                        <tr><td colspan="3" class="text-muted" style="text-align:center;padding:24px">加载中...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>`;

    // Load data
    if (currentProjectId) {
        try {
            const data = await EnhancementsAPI.getGlossary(currentProjectId);
            EnhancementsState.glossary.entries = data.entries || [];
        } catch (e) {
            EnhancementsState.glossary.entries = getDefaultGlossary();
        }
    } else {
        EnhancementsState.glossary.entries = getDefaultGlossary();
    }
    renderGlossaryTable();
}

function getDefaultGlossary() {
    // 仅作后端不可用时的兜底展示，不包含任何具体项目内容
    return [
        { id: 1, term: 'REITs', definition: '不动产投资信托基金（Real Estate Investment Trusts）' },
        { id: 2, term: '原始权益人', definition: '将基础设施资产转让给REITs的原始权利人' },
        { id: 3, term: '基金管理人', definition: '负责基金投资运作及管理的机构' },
        { id: 4, term: '基础设施项目', definition: 'REITs底层持有的基础设施资产项目' },
        { id: 5, term: '评估基准日', definition: '资产评估报告所采用的基准日期' },
        { id: 6, term: 'ABS', definition: '资产支持证券（Asset-Backed Securities）' },
        { id: 7, term: 'SPV', definition: '特殊目的载体（Special Purpose Vehicle）' },
    ];
}

function renderGlossaryTable() {
    const tbody = document.getElementById('glossaryTableBody');
    if (!tbody) return;
    const search = EnhancementsState.glossary.searchText.toLowerCase();
    const filtered = EnhancementsState.glossary.entries.filter(e =>
        !search || e.term.toLowerCase().includes(search) || e.definition.toLowerCase().includes(search)
    );
    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center;padding:24px">暂无数据</td></tr>';
        return;
    }
    tbody.innerHTML = filtered.map(entry => `
        <tr data-id="${entry.id}">
            <td><span class="enhancement-term">${entry.term}</span></td>
            <td><span class="enhancement-def">${entry.definition}</span></td>
            <td>
                <div class="flex gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="editGlossaryEntry(${entry.id})">编辑</button>
                    <button class="btn btn-ghost btn-sm" style="color:var(--error)" onclick="deleteGlossaryEntry(${entry.id})">删除</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function filterGlossary(text) {
    EnhancementsState.glossary.searchText = text;
    renderGlossaryTable();
}

function addGlossaryEntry() {
    const tbody = document.getElementById('glossaryTableBody');
    if (!tbody) return;
    const newId = Date.now();
    const newRow = document.createElement('tr');
    newRow.dataset.id = newId;
    newRow.innerHTML = `
        <td><input type="text" class="form-input" style="width:100%;padding:6px 10px" placeholder="术语名称" id="newTerm_${newId}"></td>
        <td><input type="text" class="form-input" style="width:100%;padding:6px 10px" placeholder="全称/释义" id="newDef_${newId}"></td>
        <td>
            <div class="flex gap-8">
                <button class="btn btn-primary btn-sm" onclick="saveNewGlossary(${newId})">保存</button>
                <button class="btn btn-ghost btn-sm" onclick="cancelNewGlossary(${newId})">取消</button>
            </div>
        </td>`;
    tbody.insertBefore(newRow, tbody.firstChild);
}

function saveNewGlossary(id) {
    const term = document.getElementById(`newTerm_${id}`)?.value?.trim();
    const def = document.getElementById(`newDef_${id}`)?.value?.trim();
    if (!term || !def) { enhShowToast('请填写完整术语和释义', 'warning'); return; }
    EnhancementsState.glossary.entries.unshift({ id, term, definition: def });
    renderGlossaryTable();
    enhShowToast('术语添加成功');
    // Save to backend
    if (currentProjectId) {
        EnhancementsAPI.updateGlossary(currentProjectId, EnhancementsState.glossary.entries).catch(() => {});
    }
}

function cancelNewGlossary(id) {
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (row) row.remove();
}

function editGlossaryEntry(id) {
    const entry = EnhancementsState.glossary.entries.find(e => e.id === id);
    if (!entry) return;
    const row = document.querySelector(`tr[data-id="${id}"]`);
    if (!row) return;
    row.innerHTML = `
        <td><input type="text" class="form-input" style="width:100%;padding:6px 10px" value="${entry.term}" id="editTerm_${id}"></td>
        <td><input type="text" class="form-input" style="width:100%;padding:6px 10px" value="${entry.definition}" id="editDef_${id}"></td>
        <td>
            <div class="flex gap-8">
                <button class="btn btn-primary btn-sm" onclick="saveEditGlossary(${id})">保存</button>
                <button class="btn btn-ghost btn-sm" onclick="renderGlossaryTable()">取消</button>
            </div>
        </td>`;
}

function saveEditGlossary(id) {
    const term = document.getElementById(`editTerm_${id}`)?.value?.trim();
    const def = document.getElementById(`editDef_${id}`)?.value?.trim();
    if (!term || !def) { enhShowToast('请填写完整术语和释义', 'warning'); return; }
    const entry = EnhancementsState.glossary.entries.find(e => e.id === id);
    if (entry) { entry.term = term; entry.definition = def; }
    renderGlossaryTable();
    enhShowToast('术语修改成功');
    if (currentProjectId) {
        EnhancementsAPI.updateGlossary(currentProjectId, EnhancementsState.glossary.entries).catch(() => {});
    }
}

function deleteGlossaryEntry(id) {
    if (!confirm('确定删除此术语？')) return;
    EnhancementsState.glossary.entries = EnhancementsState.glossary.entries.filter(e => e.id !== id);
    renderGlossaryTable();
    enhShowToast('术语已删除');
    if (currentProjectId) {
        EnhancementsAPI.updateGlossary(currentProjectId, EnhancementsState.glossary.entries).catch(() => {});
    }
}

// ===== 2. 承诺函选择器 =====
async function renderCommitmentSelector(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '<div class="enhancement-section"><div class="enhancement-loading"><div class="enhancement-spinner"></div><span>加载承诺函模板...</span></div></div>';

    let templates = [];
    if (currentProjectId) {
        try {
            const data = await EnhancementsAPI.getCommitments(currentProjectId);
            templates = data.templates || [];
        } catch (e) {
            templates = getDefaultCommitments();
        }
    } else {
        templates = getDefaultCommitments();
    }
    EnhancementsState.commitments.templates = templates;

    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-commitment-list" id="commitmentList">
                ${templates.map(t => renderCommitmentCard(t)).join('')}
            </div>
        </div>`;
}

function getDefaultCommitments() {
    return [
        { id: 'cmt1', name: '原始权益人关于回收资金使用的承诺函', category: '资金用途', variables: ['原始权益人名称', '基金全称', '承诺日期'], preview: '本公司作为{原始权益人名称}，就{基金全称}项目回收资金的使用事宜作出如下承诺...' },
        { id: 'cmt2', name: '关于项目合规运营的承诺函', category: '合规运营', variables: ['原始权益人名称', '项目名称', '承诺日期'], preview: '本公司承诺{项目名称}的运营管理将严格遵守相关法律法规...' },
        { id: 'cmt3', name: '关于不存在利益冲突的承诺函', category: '利益冲突', variables: ['关联方名称', '基金全称', '承诺日期'], preview: '本公司声明并承诺，与{基金全称}之间不存在尚未披露的利益冲突...' },
        { id: 'cmt4', name: '信息披露承诺函', category: '信息披露', variables: ['原始权益人名称', '基金管理人', '承诺日期'], preview: '本公司承诺将按照监管要求及时、准确、完整地披露相关信息...' },
    ];
}

function renderCommitmentCard(template) {
    const isSelected = EnhancementsState.commitments.selectedId === template.id;
    return `
        <div class="enhancement-commitment-card ${isSelected ? 'expanded' : ''}" data-id="${template.id}">
            <div class="commitment-card-header" onclick="toggleCommitmentCard('${template.id}')">
                <div class="commitment-card-left">
                    <span class="badge badge-info">${template.category}</span>
                    <span class="commitment-card-name">${template.name}</span>
                </div>
                <span class="commitment-card-arrow">${isSelected ? '▲' : '▼'}</span>
            </div>
            ${isSelected ? renderCommitmentDetail(template) : ''}
        </div>`;
}

function toggleCommitmentCard(templateId) {
    EnhancementsState.commitments.selectedId =
        EnhancementsState.commitments.selectedId === templateId ? null : templateId;
    const list = document.getElementById('commitmentList');
    if (list) {
        list.innerHTML = EnhancementsState.commitments.templates.map(t => renderCommitmentCard(t)).join('');
    }
}

function renderCommitmentDetail(template) {
    return `
        <div class="commitment-card-body">
            <div class="commitment-preview">
                <div class="commitment-preview-label">模板预览：</div>
                <div class="commitment-preview-text">${template.preview}</div>
            </div>
            <div class="commitment-variables">
                <div class="commitment-variables-label">填充变量：</div>
                ${template.variables.map(v => `
                    <div class="form-group" style="margin-bottom:10px">
                        <label>${v}</label>
                        <input type="text" class="form-input" style="width:100%;padding:6px 10px" 
                            placeholder="请输入${v}" id="cmt_var_${template.id}_${v.replace(/\s/g, '_')}">
                    </div>
                `).join('')}
            </div>
            <div class="flex gap-8" style="margin-top:12px">
                <button class="btn btn-primary btn-sm" onclick="fillCommitmentTemplate('${template.id}')">生成承诺函</button>
                <button class="btn btn-ghost btn-sm" onclick="previewCommitment('${template.id}')">实时预览</button>
            </div>
        </div>`;
}

function fillCommitmentTemplate(templateId) {
    const template = EnhancementsState.commitments.templates.find(t => t.id === templateId);
    if (!template) return;
    const variables = {};
    let allFilled = true;
    template.variables.forEach(v => {
        const input = document.getElementById(`cmt_var_${templateId}_${v.replace(/\s/g, '_')}`);
        variables[v] = input?.value?.trim() || '';
        if (!variables[v]) allFilled = false;
    });
    if (!allFilled) { enhShowToast('请填写所有变量', 'warning'); return; }
    enhShowToast('承诺函生成成功');
    if (currentProjectId) {
        EnhancementsAPI.fillCommitment(currentProjectId, templateId, variables).catch(() => {});
    }
}

function previewCommitment(templateId) {
    const template = EnhancementsState.commitments.templates.find(t => t.id === templateId);
    if (!template) return;
    let preview = template.preview;
    template.variables.forEach(v => {
        const input = document.getElementById(`cmt_var_${templateId}_${v.replace(/\s/g, '_')}`);
        const val = input?.value?.trim() || `{${v}}`;
        preview = preview.replace(new RegExp(`\\{${v}\\}`, 'g'), val);
    });
    const previewEl = document.querySelector(`.enhancement-commitment-card[data-id="${templateId}"] .commitment-preview-text`);
    if (previewEl) previewEl.textContent = preview;
}

// ===== 3. 财务数据录入 =====
async function renderFinancialInput(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '<div class="enhancement-section"><div class="enhancement-loading"><div class="enhancement-spinner"></div><span>加载财务数据模板...</span></div></div>';

    let financialData = getDefaultFinancialData();
    if (currentProjectId) {
        try {
            // 后端返回 {financial_data, template}，仅当已保存过带 rows 的数据时才采用
            const resp = await EnhancementsAPI.getFinancialData(currentProjectId);
            const saved = resp && resp.financial_data;
            if (saved && Array.isArray(saved.rows)) {
                financialData = saved;
            }
        } catch (e) {
            financialData = getDefaultFinancialData();
        }
    }
    EnhancementsState.financial = financialData;

    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-toolbar">
                <span class="text-sm text-secondary">单位：万元人民币（除特别注明外）</span>
                <button class="btn btn-primary btn-sm" onclick="saveFinancialData()">保存数据</button>
            </div>
            <div class="enhancement-table-wrap">
                <table class="data-table enhancement-financial-table">
                    <thead>
                        <tr>
                            <th style="width:30%">项目</th>
                            ${financialData.columns.map(col => `<th>${col}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody id="financialTableBody">
                        ${financialData.rows.map((row, ri) => `
                            <tr>
                                <td><strong>${row.label}</strong></td>
                                ${financialData.columns.map((col, ci) => `
                                    <td>
                                        <input type="text" class="form-input enhancement-num-input"
                                            value="${formatNumber(row.values[ci])}"
                                            onblur="onFinancialBlur(${ri},${ci},this)"
                                            onfocus="onFinancialFocus(this)">
                                    </td>
                                `).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>`;
}

function getDefaultFinancialData() {
    return {
        columns: ['2024年度', '2025年度', '2026年1-3月'],
        rows: [
            { label: '营业收入', values: [12500, 15800, 4200] },
            { label: '营业成本', values: [7800, 9200, 2500] },
            { label: '毛利润', values: [4700, 6600, 1700] },
            { label: 'EBITDA', values: [5200, 7100, 1900] },
            { label: '净利润', values: [2800, 4100, 1050] },
            { label: '经营活动现金流', values: [4500, 6200, 1600] },
            { label: '资产总额', values: [85000, 92000, 93500] },
            { label: '负债总额', values: [42000, 45000, 44800] },
            { label: '净资产', values: [43000, 47000, 48700] },
        ]
    };
}

function onFinancialFocus(input) {
    // Remove formatting on focus for editing
    const raw = parseFormattedNumber(input.value);
    if (raw !== 0) input.value = raw;
    input.select();
}

function onFinancialBlur(rowIdx, colIdx, input) {
    const val = parseFormattedNumber(input.value);
    EnhancementsState.financial.rows[rowIdx].values[colIdx] = val;
    input.value = formatNumber(val);
}

function saveFinancialData() {
    enhShowToast('财务数据保存成功');
    if (currentProjectId) {
        EnhancementsAPI.saveFinancialData(currentProjectId, EnhancementsState.financial).catch(() => {});
    }
}

// ===== 4. 不涉及标记器 =====
async function renderInapplicableMarker(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = '<div class="enhancement-section"><div class="enhancement-loading"><div class="enhancement-spinner"></div><span>加载模块结构...</span></div></div>';

    // 后端只保存已勾选的模块ID列表，树结构仍用默认结构，按ID回填勾选状态
    let markedIds = [];
    if (currentProjectId) {
        try {
            const data = await EnhancementsAPI.getInapplicable(currentProjectId);
            markedIds = data.sections || [];
        } catch (e) {
            markedIds = [];
        }
    }
    const sections = getDefaultSections();
    (function applyChecked(list) {
        list.forEach(s => {
            if (markedIds.includes(s.id)) s.checked = true;
            if (s.children) applyChecked(s.children);
        });
    })(sections);
    EnhancementsState.inapplicable.sections = sections;

    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-toolbar">
                <div class="flex items-center gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="inapplicableSelectAll()">全选</button>
                    <button class="btn btn-ghost btn-sm" onclick="inapplicableDeselectAll()">全不选</button>
                </div>
                <button class="btn btn-primary btn-sm" onclick="saveInapplicable()">保存标记</button>
            </div>
            <div class="enhancement-tree" id="inapplicableTree">
                ${renderSectionTree(sections)}
            </div>
        </div>`;
}

function getDefaultSections() {
    return [
        { id: 's1', name: '一、项目基本情况', checked: false, children: [
            { id: 's1_1', name: '（一）项目概况', checked: false },
            { id: 's1_2', name: '（二）特殊限定情况说明', checked: true },
            { id: 's1_3', name: '（三）可扩募资产情况', checked: false },
        ]},
        { id: 's2', name: '二、参与主体情况', checked: false, children: [
            { id: 's2_1', name: '（一）原始权益人', checked: false },
            { id: 's2_2', name: '（二）基金管理人', checked: false },
            { id: 's2_3', name: '（三）资产支持证券管理人', checked: false },
            { id: 's2_4', name: '（四）托管人', checked: false },
        ]},
        { id: 's3', name: '三、REITs设立方案', checked: false, children: [
            { id: 's3_1', name: '（一）交易结构', checked: false },
            { id: 's3_2', name: '（二）基金产品方案', checked: false },
            { id: 's3_3', name: '（三）ABS方案', checked: false },
        ]},
        { id: 's4', name: '四、项目基本条件', checked: false, children: [
            { id: 's4_1', name: '（一）合规经营情况', checked: false },
            { id: 's4_2', name: '（二）收益分配情况', checked: false },
            { id: 's4_3', name: '（三）经营期限', checked: false },
        ]},
        { id: 's5', name: '五、项目合规情况', checked: false, children: [
            { id: 's5_1', name: '（一）项目建设合规', checked: false },
            { id: 's5_2', name: '（二）土地使用权', checked: false },
            { id: 's5_3', name: '（三）环评批复', checked: false },
        ]},
        { id: 's6', name: '六、运营管理安排', checked: false, children: [
            { id: 's6_1', name: '（一）运营管理机构', checked: false },
            { id: 's6_2', name: '（二）运营管理制度', checked: false },
        ]},
        { id: 's7', name: '七、募集资金用途情况', checked: false, children: [
            { id: 's7_1', name: '（一）募集资金用途', checked: false },
            { id: 's7_2', name: '（二）回收资金使用计划', checked: false },
        ]},
    ];
}

function renderSectionTree(sections, level = 0) {
    return sections.map(s => `
        <div class="enhancement-tree-node" style="padding-left:${level * 20}px">
            <label class="enhancement-checkbox-label">
                <input type="checkbox" ${s.checked ? 'checked' : ''} 
                    onchange="toggleInapplicable('${s.id}', this.checked)">
                <span class="${s.checked ? 'enhancement-text-strikethrough' : ''}">${s.name}</span>
                ${s.checked ? '<span class="badge badge-warning" style="margin-left:8px;font-size:10px">不涉及</span>' : ''}
            </label>
            ${s.children ? renderSectionTree(s.children, level + 1) : ''}
        </div>
    `).join('');
}

function toggleInapplicable(sectionId, checked) {
    function updateSection(sections) {
        for (let s of sections) {
            if (s.id === sectionId) { s.checked = checked; return true; }
            if (s.children && updateSection(s.children)) return true;
        }
        return false;
    }
    updateSection(EnhancementsState.inapplicable.sections);
    const tree = document.getElementById('inapplicableTree');
    if (tree) tree.innerHTML = renderSectionTree(EnhancementsState.inapplicable.sections);
}

function inapplicableSelectAll() {
    function setAll(sections, val) { sections.forEach(s => { s.checked = val; if (s.children) setAll(s.children, val); }); }
    setAll(EnhancementsState.inapplicable.sections, true);
    const tree = document.getElementById('inapplicableTree');
    if (tree) tree.innerHTML = renderSectionTree(EnhancementsState.inapplicable.sections);
}

function inapplicableDeselectAll() {
    function setAll(sections, val) { sections.forEach(s => { s.checked = val; if (s.children) setAll(s.children, val); }); }
    setAll(EnhancementsState.inapplicable.sections, false);
    const tree = document.getElementById('inapplicableTree');
    if (tree) tree.innerHTML = renderSectionTree(EnhancementsState.inapplicable.sections);
}

function saveInapplicable() {
    function getCheckedIds(sections) {
        let ids = [];
        sections.forEach(s => { if (s.checked) ids.push(s.id); if (s.children) ids = ids.concat(getCheckedIds(s.children)); });
        return ids;
    }
    const ids = getCheckedIds(EnhancementsState.inapplicable.sections);
    enhShowToast(`已标记 ${ids.length} 个模块为"不涉及"`);
    if (currentProjectId) {
        EnhancementsAPI.updateInapplicable(currentProjectId, ids, '不涉及/不适用').catch(() => {});
    }
}

// ===== 5. 基准日配置 =====
async function renderQueryDateConfig(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    let dates = getDefaultDates();
    if (currentProjectId) {
        try {
            // 后端返回 {base_date, query_point, extra}，反向还原为前端的日期字段
            const data = await EnhancementsAPI.getQueryDates(currentProjectId);
            dates = { ...dates, ...(data.extra || {}) };
            if (data.base_date) dates.evaluation_date = data.base_date;
            if (data.query_point) dates.report_date = data.query_point;
        } catch (e) {
            dates = getDefaultDates();
        }
    }
    EnhancementsState.queryDates.dates = dates;

    const dateFields = [
        { key: 'evaluation_date', label: '评估基准日', desc: '资产评估报告基准日期' },
        { key: 'audit_date', label: '审计基准日', desc: '审计报告基准日期' },
        { key: 'report_date', label: '申报日期', desc: '向发改委提交申报的日期' },
        { key: 'financial_date', label: '财务数据截止日', desc: '财务数据披露的截止日期' },
        { key: 'legal_opinion_date', label: '法律意见书基准日', desc: '法律意见书出具基准日期' },
        { key: 'due_diligence_date', label: '尽调基准日', desc: '尽职调查完成日期' },
    ];

    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-date-grid">
                ${dateFields.map(f => `
                    <div class="enhancement-date-item">
                        <label>${f.label}</label>
                        <input type="date" class="form-input" style="width:100%;padding:8px 12px"
                            value="${dates[f.key] || ''}"
                            onchange="onQueryDateChange('${f.key}', this.value)">
                        <span class="text-sm text-muted">${f.desc}</span>
                    </div>
                `).join('')}
            </div>
            <div style="margin-top:16px;text-align:right">
                <button class="btn btn-primary btn-sm" onclick="saveQueryDates()">保存日期配置</button>
            </div>
        </div>`;
}

function getDefaultDates() {
    return {
        evaluation_date: '2026-03-31',
        audit_date: '2026-03-31',
        report_date: '2026-07-01',
        financial_date: '2026-03-31',
        legal_opinion_date: '2026-06-15',
        due_diligence_date: '2026-06-01',
    };
}

function onQueryDateChange(key, value) {
    EnhancementsState.queryDates.dates[key] = value;
}

function saveQueryDates() {
    enhShowToast('基准日配置保存成功');
    if (currentProjectId) {
        EnhancementsAPI.updateQueryDates(currentProjectId, EnhancementsState.queryDates.dates).catch(() => {});
    }
}

// ===== 6. 附件管理 =====
async function renderAttachmentManager(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    let attachments = [];
    if (currentProjectId) {
        try {
            const data = await EnhancementsAPI.getAttachments(currentProjectId);
            // 后端条目为 {id, title, filename}，转回前端展示用的 {id, name, type}
            attachments = (data.attachments || []).map(a => ({
                id: a.id || ('att_' + Math.random().toString(36).slice(2, 8)),
                name: a.title,
                type: a.filename || 'PDF',
                number: a.number ? `附件${a.number}` : '',
            }));
        } catch (e) {
            attachments = getDefaultAttachments();
        }
    } else {
        attachments = getDefaultAttachments();
    }
    EnhancementsState.attachments.items = attachments;

    container.innerHTML = `
        <div class="enhancement-section">
            <div class="enhancement-toolbar">
                <span class="text-sm text-secondary">拖拽行可调整顺序，编号自动更新</span>
                <div class="flex gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="addAttachmentItem()">+ 新增附件</button>
                    <button class="btn btn-primary btn-sm" onclick="saveAttachments()">保存</button>
                </div>
            </div>
            <div class="enhancement-attachment-list" id="attachmentList">
                ${renderAttachmentItems(attachments)}
            </div>
        </div>`;

    initAttachmentDragDrop();
}

function getDefaultAttachments() {
    return [
        { id: 'att1', number: '附件1', name: '原始权益人营业执照', type: 'PDF' },
        { id: 'att2', number: '附件2', name: '基础设施项目权属证明', type: 'PDF' },
        { id: 'att3', number: '附件3', name: '评估报告', type: 'PDF' },
        { id: 'att4', number: '附件4', name: '审计报告', type: 'PDF' },
        { id: 'att5', number: '附件5', name: '法律意见书', type: 'DOCX' },
        { id: 'att6', number: '附件6', name: '项目合规证明材料', type: 'PDF' },
        { id: 'att7', number: '附件7', name: '运营数据表', type: 'XLSX' },
        { id: 'att8', number: '附件8', name: '原始权益人关于回收资金使用的承诺函', type: 'PDF' },
    ];
}

function renderAttachmentItems(items) {
    return items.map((item, idx) => `
        <div class="enhancement-attachment-item" draggable="true" data-id="${item.id}" data-index="${idx}">
            <div class="attachment-drag-handle">⋮⋮</div>
            <div class="attachment-number">附件${idx + 1}</div>
            <div class="attachment-name">${item.name}</div>
            <span class="badge ${item.type === 'PDF' ? 'badge-error' : item.type === 'XLSX' ? 'badge-success' : 'badge-info'}">${item.type}</span>
            <div class="attachment-actions">
                <button class="btn btn-ghost btn-sm" onclick="editAttachment('${item.id}')">编辑</button>
                <button class="btn btn-ghost btn-sm" style="color:var(--error)" onclick="removeAttachment('${item.id}')">删除</button>
            </div>
        </div>
    `).join('');
}

function addAttachmentItem() {
    const newId = 'att_' + Date.now();
    EnhancementsState.attachments.items.push({ id: newId, number: '', name: '新附件', type: 'PDF' });
    refreshAttachmentList();
    // Auto trigger edit on new item
    setTimeout(() => editAttachment(newId), 100);
}

function editAttachment(id) {
    const item = EnhancementsState.attachments.items.find(a => a.id === id);
    if (!item) return;
    const el = document.querySelector(`.enhancement-attachment-item[data-id="${id}"]`);
    if (!el) return;
    el.innerHTML = `
        <div class="attachment-drag-handle">⋮⋮</div>
        <div class="attachment-number">${item.number || '附件'}</div>
        <input type="text" class="form-input" style="flex:1;padding:6px 10px" value="${item.name}" id="editAttName_${id}">
        <select class="form-input" style="width:80px;padding:6px 8px" id="editAttType_${id}">
            <option ${item.type === 'PDF' ? 'selected' : ''}>PDF</option>
            <option ${item.type === 'DOCX' ? 'selected' : ''}>DOCX</option>
            <option ${item.type === 'XLSX' ? 'selected' : ''}>XLSX</option>
        </select>
        <div class="attachment-actions">
            <button class="btn btn-primary btn-sm" onclick="saveAttachmentEdit('${id}')">确定</button>
            <button class="btn btn-ghost btn-sm" onclick="refreshAttachmentList()">取消</button>
        </div>`;
}

function saveAttachmentEdit(id) {
    const item = EnhancementsState.attachments.items.find(a => a.id === id);
    if (!item) return;
    const name = document.getElementById(`editAttName_${id}`)?.value?.trim();
    const type = document.getElementById(`editAttType_${id}`)?.value;
    if (name) item.name = name;
    if (type) item.type = type;
    refreshAttachmentList();
    enhShowToast('附件信息已更新');
}

function removeAttachment(id) {
    if (!confirm('确定删除此附件？')) return;
    EnhancementsState.attachments.items = EnhancementsState.attachments.items.filter(a => a.id !== id);
    refreshAttachmentList();
    enhShowToast('附件已删除');
}

function refreshAttachmentList() {
    // Re-number
    EnhancementsState.attachments.items.forEach((item, idx) => { item.number = `附件${idx + 1}`; });
    const list = document.getElementById('attachmentList');
    if (list) list.innerHTML = renderAttachmentItems(EnhancementsState.attachments.items);
    initAttachmentDragDrop();
}

function saveAttachments() {
    enhShowToast('附件列表保存成功');
    if (currentProjectId) {
        EnhancementsAPI.updateAttachments(currentProjectId, EnhancementsState.attachments.items).catch(() => {});
    }
}

// Drag & Drop for attachments
function initAttachmentDragDrop() {
    const list = document.getElementById('attachmentList');
    if (!list) return;
    let dragSrcEl = null;

    list.querySelectorAll('.enhancement-attachment-item').forEach(item => {
        item.addEventListener('dragstart', function(e) {
            dragSrcEl = this;
            this.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', this.dataset.index);
        });
        item.addEventListener('dragover', function(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            this.classList.add('drag-over');
        });
        item.addEventListener('dragleave', function() {
            this.classList.remove('drag-over');
        });
        item.addEventListener('drop', function(e) {
            e.preventDefault();
            this.classList.remove('drag-over');
            if (dragSrcEl === this) return;
            const fromIdx = parseInt(e.dataTransfer.getData('text/plain'));
            const toIdx = parseInt(this.dataset.index);
            const items = EnhancementsState.attachments.items;
            const [moved] = items.splice(fromIdx, 1);
            items.splice(toIdx, 0, moved);
            refreshAttachmentList();
        });
        item.addEventListener('dragend', function() {
            this.classList.remove('dragging');
            list.querySelectorAll('.enhancement-attachment-item').forEach(i => i.classList.remove('drag-over'));
        });
    });
}

// ===== 初始化绑定增强功能Tab事件 =====
document.addEventListener('DOMContentLoaded', function() {
    // Bind enhancement tab click events
    document.querySelectorAll('.enhancement-tabs .tab-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            switchEnhancementTab(this.dataset.tab);
        });
    });
});
