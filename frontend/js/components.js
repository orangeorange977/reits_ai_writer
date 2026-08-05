/**
 * REIT-AI 法律文件生成系统 - UI组件渲染模块
 * 
 * 提供可复用的UI组件渲染函数，用于动态生成页面内容。
 */

/**
 * 渲染统计卡片
 * @param {HTMLElement} container - 容器元素
 * @param {Array} data - 统计数据 [{icon, value, label, color}]
 */
function renderStatCards(container, data) {
    if (!container || !data) return;

    const html = data.map(item => `
        <div class="stat-card">
            <div class="stat-icon ${item.color || 'blue'}">${item.icon}</div>
            <div class="stat-info">
                <div class="stat-value">${item.value}</div>
                <div class="stat-label">${item.label}</div>
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

/**
 * 渲染项目表格
 * @param {HTMLElement} container - 容器元素(tbody)
 * @param {Array} projects - 项目数据 [{id, name, assetType, stage, status, updateTime}]
 */
function renderProjectTable(container, projects) {
    if (!container || !projects) return;

    const statusBadgeMap = {
        '生成中': 'badge-warning',
        '已完成': 'badge-success',
        '待处理': 'badge-info',
        '已扫描': 'badge-info',
        '错误': 'badge-error'
    };

    const html = projects.map(proj => `
        <tr>
            <td><strong>${proj.name}</strong></td>
            <td><span class="badge badge-info">${proj.assetType}</span></td>
            <td><span class="badge badge-primary">${proj.stage}</span></td>
            <td><span class="badge ${statusBadgeMap[proj.status] || 'badge-info'}">${proj.status}</span></td>
            <td class="text-sm text-muted">${proj.updateTime}</td>
            <td>
                <button class="btn btn-ghost btn-sm" onclick="selectProject(${proj.id})">编辑</button>
            </td>
        </tr>
    `).join('');

    container.innerHTML = html;
}

/**
 * 渲染文件列表
 * @param {HTMLElement} container - 容器元素
 * @param {Array} files - 文件数据 [{name, type, size}]
 */
function renderFileList(container, files) {
    if (!container || !files) return;

    const typeLabels = {
        'pdf': 'PDF',
        'docx': 'DOCX',
        'xlsx': 'XLSX',
        'doc': 'DOC',
        'xls': 'XLS'
    };

    const html = files.map(file => {
        const typeClass = file.type || 'other';
        const typeLabel = typeLabels[file.type] || file.type.toUpperCase();

        return `
            <div class="file-item">
                <div class="file-icon ${typeClass}">${typeLabel}</div>
                <div class="file-info">
                    <div class="file-name">${file.name}</div>
                    <div class="file-meta">${file.size || ''} · ${typeLabel}文件</div>
                </div>
                <div class="file-actions">
                    <button class="btn btn-ghost btn-sm" onclick="showToast('查看文件: ${file.name}')">查看</button>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

/**
 * 渲染七章步骤条
 * @param {HTMLElement} container - 容器元素
 * @param {Array} chapters - 章节数据 [{title, status, desc}]
 *   status: 'done' | 'current' | 'active' | '' (pending)
 */
function renderStepper(container, chapters) {
    if (!container || !chapters) return;

    const html = chapters.map((ch, index) => {
        const isLast = index === chapters.length - 1;
        // 圆圈内容：优先用自定义 circle；否则 done 显示✓、其余显示序号
        let circleContent = ch.circle != null
            ? ch.circle
            : (ch.status === 'done' ? '✓' : index + 1);
        // 点击动作：优先用自定义 onClick；否则默认按章节序号
        const onClick = ch.onClick || `selectChapter(${index + 1})`;

        return `
            <div class="step ${ch.status}" data-chapter="${index + 1}" onclick="${onClick}">
                ${!isLast ? '<div class="step-connector"></div>' : ''}
                <div class="step-circle">${circleContent}</div>
                <div class="step-title">${ch.title}</div>
                <div class="step-desc">${ch.desc || ''}</div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

/**
 * 渲染章节详情（树形结构）
 * @param {HTMLElement} container - 容器元素
 * @param {object} chapterData - 章节数据 { title, status, sections: [...] }
 */
function renderChapterDetail(container, chapterData) {
    if (!container || !chapterData) return;

    const statusBadge = {
        'done': '<span class="badge badge-success">已提取</span>',
        'current': '<span class="badge badge-warning">提取中</span>',
        'active': '<span class="badge badge-info">待生成</span>',
        '': '<span class="badge badge-info">待提取</span>'
    };

    const sectionsHtml = (chapterData.sections || []).map(section => {
        if (section.has_subsections && section.subsections) {
            // 有子模块的section - 渲染为可展开的树节点
            const subsectionsHtml = section.subsections.map(sub => {
                const fieldsHtml = _renderFields(sub.fields || []);
                return `
                    <div class="subsection-item">
                        <div class="subsection-header" onclick="toggleSubsection(this)">
                            <span class="toggle-icon">▶</span>
                            <span class="subsection-title">${sub.title}</span>
                            <span class="field-status">${_getFieldStatus(sub.fields)}</span>
                        </div>
                        <div class="subsection-body" style="display:none;">
                            ${fieldsHtml}
                        </div>
                    </div>
                `;
            }).join('');

            return `
                <div class="section-item has-subsections">
                    <div class="section-header" onclick="toggleSection(this)">
                        <span class="toggle-icon">▼</span>
                        <span class="section-title">${section.title}</span>
                    </div>
                    <div class="section-body">
                        ${subsectionsHtml}
                    </div>
                </div>
            `;
        } else {
            // 叶子section - 直接显示fields
            const fieldsHtml = _renderFields(section.fields || []);
            return `
                <div class="section-item">
                    <div class="section-header" onclick="toggleSection(this)">
                        <span class="toggle-icon">▶</span>
                        <span class="section-title">${section.title}</span>
                        <span class="field-status">${_getFieldStatus(section.fields)}</span>
                    </div>
                    <div class="section-body" style="display:none;">
                        ${fieldsHtml}
                    </div>
                </div>
            `;
        }
    }).join('');

    container.innerHTML = `
        <div class="chapter-detail-header">
            <div class="flex items-center gap-12">
                <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">${chapterData.title}</h3>
                ${statusBadge[chapterData.status] || statusBadge['']}
            </div>
            <div class="flex gap-8">
                <button class="btn btn-ghost btn-sm" onclick="navigate('chapter-edit')">编辑</button>
                <button class="btn btn-primary btn-sm" onclick="previewChapter()">预览</button>
            </div>
        </div>
        <div class="chapter-detail-body">
            <div class="sections-tree">
                ${sectionsHtml}
            </div>
        </div>
    `;
}

/**
 * 渲染字段列表
 */
function _renderFields(fields) {
    if (!fields || fields.length === 0) return '<div class="text-muted text-sm" style="padding:8px 0;">暂无字段</div>';
    return fields.map(field => {
        if (field.type === 'table') {
            return _renderTableField(field);
        }
        if (field.type === 'form_table') {
            return _renderFormTableField(field);
        }
        return `
            <div class="field-item">
                <span class="field-label">${field.label}</span>
                <span class="field-value ${!field.value ? 'pending' : ''}">${field.value || '（待提取）'}</span>
                <span class="field-source">来源：${field.source || '待提取'}</span>
                <button class="btn btn-ghost btn-sm" onclick="editField(this)">编辑</button>
            </div>
        `;
    }).join('');
}

/**
 * 渲染官方模板表格字段
 */
function _renderTableField(field) {
    const columns = field.columns || [];
    const rows = Array.isArray(field.value) ? field.value : [];
    
    let html = '<div class="field-item field-table-item">';
    html += `<span class="field-label">${field.label}</span>`;
    
    // 前置文本框（如有template_text）
    if (field.template_text !== undefined) {
        html += `<div class="table-prefix-text">
            <textarea class="table-text-input" placeholder="请输入表格前的文字说明">${field.template_text || ''}</textarea>
        </div>`;
    }
    
    // 渲染HTML表格
    if (columns.length > 0) {
        const headerHtml = columns.map(col => `<th>${col}</th>`).join('');
        let bodyHtml = '';
        if (rows.length > 0) {
            bodyHtml = rows.map(row => {
                const cells = Array.isArray(row) 
                    ? row.map(cell => `<td contenteditable="true">${cell || ''}</td>`).join('')
                    : columns.map((_, i) => `<td contenteditable="true">${row[i] || ''}</td>`).join('');
                return `<tr>${cells}</tr>`;
            }).join('');
        } else {
            // 空表格显示3行占位
            bodyHtml = Array(3).fill(null).map(() => 
                `<tr>${columns.map(() => '<td contenteditable="true" class="empty-cell"></td>').join('')}</tr>`
            ).join('');
        }
        
        html += `<div class="official-table-container">
            <table class="official-table">
                <thead><tr>${headerHtml}</tr></thead>
                <tbody>${bodyHtml}</tbody>
            </table>
        </div>`;
    } else {
        html += '<div class="text-muted text-sm">（表格结构未定义）</div>';
    }
    
    html += `<button class="btn btn-ghost btn-sm" onclick="editField(this)">编辑</button>`;
    html += '</div>';
    return html;
}

/**
 * 渲染官方模板表单式表格（左列标签、右列填写）
 */
function _renderFormTableField(field) {
    const rows = field.rows || [];
    const values = field.value || {};
    
    let html = '<div class="field-item field-table-item">';
    html += `<div class="form-table-title">${field.label}</div>`;
    
    // 渲染表单式表格
    html += '<div class="official-table-container">';
    html += '<table class="official-table form-table">';
    html += '<tbody>';
    
    rows.forEach(row => {
        const val = values[row.id] || '';
        html += `<tr>
            <td class="form-table-label">${row.label}</td>
            <td class="form-table-value" contenteditable="true" data-field-id="${row.id}">${val}</td>
        </tr>`;
    });
    
    html += '</tbody></table></div>';
    html += '</div>';
    return html;
}

/**
 * 获取字段填写状态
 */
function _getFieldStatus(fields) {
    if (!fields || fields.length === 0) return '';
    const filled = fields.filter(f => f.value).length;
    if (filled === 0) return '(待提取)';
    if (filled === fields.length) return '(已完成)';
    return `(${filled}/${fields.length})`;
}

/**
 * 展开/折叠section
 */
function toggleSection(header) {
    const body = header.nextElementSibling;
    const icon = header.querySelector('.toggle-icon');
    if (body.style.display === 'none') {
        body.style.display = 'block';
        icon.textContent = '▼';
    } else {
        body.style.display = 'none';
        icon.textContent = '▶';
    }
}

/**
 * 展开/折叠subsection
 */
function toggleSubsection(header) {
    toggleSection(header);
}

/**
 * 渲染进度条
 * @param {HTMLElement} container - 容器元素
 * @param {number} percent - 进度百分比 (0-100)
 * @param {string} label - 进度标签
 * @param {string} color - 颜色类名: 'blue' | 'green' | 'orange'
 */
function renderProgressBar(container, percent, label, color = 'blue') {
    if (!container) return;

    const clampedPercent = Math.max(0, Math.min(100, percent));

    container.innerHTML = `
        <div class="progress-label">
            <span>${label || '进度'}</span>
            <span>${clampedPercent}%</span>
        </div>
        <div class="progress-bar">
            <div class="progress-fill ${color}" style="width:${clampedPercent}%"></div>
        </div>
    `;
}

/**
 * 渲染时间线
 * @param {HTMLElement} container - 容器元素
 * @param {Array} events - 事件列表 [{title, time, desc, color}]
 *   color: 'blue' | 'green' | 'orange' | 'red' | 'gray'
 */
function renderTimeline(container, events) {
    if (!container || !events) return;

    const html = events.map(event => `
        <div class="timeline-item">
            <div class="timeline-dot ${event.color || 'blue'}"></div>
            <div class="timeline-content">
                <div class="timeline-title">${event.title}</div>
                <div class="timeline-time">${event.time || ''}</div>
                ${event.desc ? `<div class="timeline-desc">${event.desc}</div>` : ''}
            </div>
        </div>
    `).join('');

    container.innerHTML = html;
}

/**
 * 渲染文档表格
 * @param {HTMLElement} container - tbody容器
 * @param {Array} documents - 文档数据 [{id, name, type, time, size, status}]
 */
function renderDocTable(container, documents) {
    if (!container || !documents) return;

    const statusMap = {
        '已完成': 'badge-success',
        '待审核': 'badge-warning',
        '生成中': 'badge-info',
        '失败': 'badge-error'
    };

    const typeColorMap = {
        'pdf': 'var(--error)',
        'docx': 'var(--info)',
        'xlsx': 'var(--success)'
    };

    const html = documents.map(doc => `
        <tr>
            <td>
                <div class="flex items-center gap-8">
                    <span style="color:${typeColorMap[doc.type] || 'var(--text-muted)'};font-weight:700;font-size:12px">${(doc.type || '').toUpperCase()}</span>
                    <strong>${doc.name}</strong>
                </div>
            </td>
            <td class="text-sm text-muted">${doc.time}</td>
            <td class="text-sm">${doc.size}</td>
            <td><span class="badge ${statusMap[doc.status] || 'badge-info'}">${doc.status}</span></td>
            <td>
                <div class="flex gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="downloadDoc('${doc.id}')">下载</button>
                    <button class="btn btn-ghost btn-sm" onclick="regenerateDoc('${doc.id}')">重新生成</button>
                    <button class="btn btn-ghost btn-sm" style="color:var(--error)" onclick="deleteDoc('${doc.id}')">删除</button>
                </div>
            </td>
        </tr>
    `).join('');

    container.innerHTML = html;
}

/**
 * 渲染上传区域
 * @param {HTMLElement} container - 容器元素
 * @param {object} options - 配置项 {title, desc, accept}
 */
function renderUploadZone(container, options = {}) {
    if (!container) return;

    const title = options.title || '点击或拖拽文件到此处上传';
    const desc = options.desc || '支持 PDF、DOCX、XLSX 格式文件';

    container.innerHTML = `
        <div class="upload-zone" onclick="document.getElementById('fileUploadInput').click()">
            <div class="uz-icon">📁</div>
            <div class="uz-title">${title}</div>
            <div class="uz-desc">${desc}</div>
        </div>
        <input type="file" id="fileUploadInput" style="display:none" 
            accept="${options.accept || '.pdf,.docx,.xlsx'}" multiple>
    `;
}

/**
 * 渲染Tab标签栏
 * @param {HTMLElement} container - 容器元素
 * @param {Array} tabs - 标签数据 [{id, label, active}]
 * @param {Function} onSwitch - 切换回调函数
 */
function renderTabBar(container, tabs, onSwitch) {
    if (!container || !tabs) return;

    const html = tabs.map(tab => `
        <div class="tab-item ${tab.active ? 'active' : ''}" data-tab="${tab.id}" onclick="handleTabSwitch(this, '${tab.id}')">${tab.label}</div>
    `).join('');

    container.innerHTML = html;

    // 保存回调
    container._onSwitch = onSwitch;
}

/**
 * Tab切换处理
 */
function handleTabSwitch(el, tabId) {
    const tabBar = el.closest('.tab-bar');
    tabBar.querySelectorAll('.tab-item').forEach(item => item.classList.remove('active'));
    el.classList.add('active');

    if (tabBar._onSwitch) {
        tabBar._onSwitch(tabId);
    }
}

/**
 * 创建加载指示器
 * @param {string} text - 加载文案
 * @returns {string} HTML字符串
 */
function createLoader(text = '加载中...') {
    return `
        <div style="text-align:center;padding:48px 0;color:var(--text-muted)">
            <div style="font-size:24px;margin-bottom:12px;animation:pulse 1.5s infinite">⏳</div>
            <div style="font-size:13px">${text}</div>
        </div>
    `;
}

/**
 * 创建空状态占位
 * @param {string} text - 空状态文案
 * @param {string} icon - 图标
 * @returns {string} HTML字符串
 */
function createEmptyState(text = '暂无数据', icon = '📭') {
    return `
        <div style="text-align:center;padding:48px 0;color:var(--text-muted)">
            <div style="font-size:36px;margin-bottom:12px">${icon}</div>
            <div style="font-size:14px">${text}</div>
        </div>
    `;
}
