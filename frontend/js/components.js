/**
 * REIT-AI 法律文件生成系统 - UI组件渲染模块
 * 
 * 提供可复用的UI组件渲染函数，用于动态生成页面内容。
 * 旧管线的字段表单/文档表格等渲染器已随步骤 2.6 删除。
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
                ${proj.isDemo ? '' : `<button class="btn btn-ghost btn-sm" style="color:#d9534f" onclick="confirmDeleteProject(${proj.id})">删除</button>`}
            </td>
        </tr>
    `).join('');

    container.innerHTML = html;
}

/**
 * 渲染章节步骤条（章节项由项目绑定的模板包提供）
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
