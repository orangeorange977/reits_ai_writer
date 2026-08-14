/**
 * REIT-AI 法律文件生成系统 - UI组件渲染模块
 * 
 * 提供可复用的UI组件渲染函数，用于动态生成页面内容。
 * 旧管线的字段表单/文档表格等渲染器已随步骤 2.6 删除。
 */

/** 字节风格线性图标统一出口：stroke 跟随 currentColor，尺寸由容器 .icn 规则控制 */
const ICN = {
    folder: '<svg class="icn" viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>',
    file: '<svg class="icn" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>',
    upload: '<svg class="icn" viewBox="0 0 24 24"><path d="M12 15V4m0 0 4 4m-4-4-4 4M4 20h16"/></svg>',
    download: '<svg class="icn" viewBox="0 0 24 24"><path d="M12 4v11m0 0 4-4m-4 4-4-4M4 20h16"/></svg>',
    plus: '<svg class="icn" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    edit: '<svg class="icn" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    save: '<svg class="icn" viewBox="0 0 24 24"><path d="M17 21v-8H7v8M7 3v5h8"/><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/></svg>',
    robot: '<svg class="icn" viewBox="0 0 24 24"><rect x="5" y="9" width="14" height="10" rx="2"/><path d="M12 9V5"/><circle cx="12" cy="4" r="1"/><path d="M9.5 13v1.5M14.5 13v1.5"/></svg>',
    chat: '<svg class="icn" viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z"/></svg>',
    image: '<svg class="icn" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m5 19 5-5 3 3 4-4 2 2"/></svg>',
    trash: '<svg class="icn" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>',
    refresh: '<svg class="icn" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v5h-5"/></svg>',
    check: '<svg class="icn" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8.5 12.3 2.4 2.4 4.8-4.8"/></svg>',
    grid: '<svg class="icn" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>',
};

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
                <button class="btn btn-primary btn-sm" onclick="selectProject(${proj.id})">进入</button>
                <button class="btn btn-ghost btn-sm" onclick="openEditProject(${proj.id})">编辑</button>
                ${proj.isDemo ? '' : `<button class="btn btn-ghost btn-sm" style="color:#d9534f" onclick="confirmDeleteProject(${proj.id})" title="删除该项目及其数据">删除</button>`}
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
