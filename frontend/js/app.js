/**
 * REIT-AI 法律文件生成系统 - 主应用逻辑
 */

// 页面标题映射
const PAGE_TITLES = {
    'overview': '系统概览',
    'ndrc': '材料生成',
    'knowhow': 'Know-how',
    'documents': '文档管理',
    'evaluation': '对比评测',
    'skills': 'Skill 管理',
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
    _refreshTopbarProjectSwitcher();

    // 页面切换后加载对应数据
    onPageEnter(pageId);
}

/** 顶栏项目切换器：所有项目相关页面（申报材料/数据提取/Know-how/Skill 管理/文档管理/
 * 对比评测）共用同一个 currentProjectId，之前只能回系统概览才能换项目——这里补一个随
 * 处可见的切换入口，不用来回跳转。 */
function _refreshTopbarProjectSwitcher() {
    const sel = document.getElementById('topbarProjectSwitcher');
    if (!sel) return;
    const projects = _projectsCache || [];
    if (!projects.length || currentPage === 'overview') { sel.style.display = 'none'; return; }
    sel.style.display = '';
    sel.innerHTML = projects.map(p =>
        `<option value="${_escHtmlAttr(p.id)}" ${String(p.id) === String(currentProjectId) ? 'selected' : ''}>${_escHtmlAttr(p.name || ('项目 ' + p.id))}</option>`
    ).join('');
}

/** 切换当前项目：更新全局 currentProjectId，然后按当前所在页面重新加载该项目的内容。 */
async function switchCurrentProject(projectId) {
    if (!projectId || projectId === currentProjectId) return;
    currentProjectId = projectId;
    updateProjectHeaderBar();
    await onPageEnter(currentPage);
    showToast('已切换到 ' + ((_projectsCache.find(p => String(p.id) === String(projectId)) || {}).name || ('项目 ' + projectId)));
}

/** 侧边栏“数据提取”入口：复用申报材料页的数据工作台容器，但直接跳到数据提取视图，
 * 不经过章节步骤条——业务反馈数据提取应有独立入口，不该混在章节流程里。 */
async function goToDataExtraction() {
    // 不走 navigate('ndrc')：那条路径会异步跑 loadChapters()，它默认落地到"请选择小节"的
    // 空态，可能在这里的数据视图渲染完之后才回来，把内容覆盖掉（竞态）。这里手动控制页面
    // 显示，只做数据提取需要的那几步，跳过默认落地。
    document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
    document.getElementById('page-ndrc')?.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    document.querySelector('.nav-item[data-page="data-extract"]')?.classList.add('active');
    const titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = '数据提取';
    currentPage = 'ndrc';
    _setNdrcMode('extract');
    _refreshTopbarProjectSwitcher();
    if (!currentProjectId) return;
    await loadProjectPack();
    await selectDataFoundation();
    loadMaterialsUI();
}

/** page-ndrc 被"数据提取"和"文档生成"两个侧边栏入口共用：按模式切换该显示材料面板
 * 还是章节步骤条——两边只应该看到各自需要的部分，不是全部堆在一起。 */
function _setNdrcMode(mode) {
    const materials = document.getElementById('materialsPanelWrap');
    const stepper = document.getElementById('stepperCardWrap');
    if (materials) materials.style.display = mode === 'extract' ? '' : 'none';
    if (stepper) stepper.style.display = mode === 'extract' ? 'none' : '';
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
        case 'knowhow':
            if (typeof knowhowPageEnter === 'function') {
                await knowhowPageEnter();
            }
            break;
        case 'documents':
            if (currentProjectId) {
                await loadDocuments();
            }
            break;
        case 'evaluation':
            if (typeof evalPageEnter === 'function') {
                evalPageEnter();
            }
            break;
        case 'skills':
            if (typeof skillsPageEnter === 'function') {
                skillsPageEnter();
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

/** 按文件名（可无目录）模糊匹配材料库中的真实路径。
 * AI 常把官方全称简化（如“9-1 法律意见书.docx” vs 磁盘“9-1 律师事务所就项目权属…出具的法律意见书.docx”），
 * 因此除精确/互含外，还用“编号前缀相同 + 核心词命中”规则兜底。 */
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
    if (!hit) {
        // 文号强匹配：报告类文件的文号是唯一标识（“容诚审字[2024]518Z0922号”“JLL-BJ[2025]房估字第0001号”）
        const dn = (name.match(/[（(]([^（）()]*\[\d{4}\][^（）()]*)[）)]/) || [])[1];
        if (dn) {
            const core = dn.replace(/\s+/g, '');
            hit = files.find(p => base(p).replace(/\s+/g, '').includes(core)) || null;
        }
    }
    if (!hit) {
        // 编号+核心词：依据里“9-1 法律意见书” → 磁盘上同为“9-1 …”开头且含“法律意见书”的文件
        const m = name.match(/^(\d+(?:[-—]\d+)?)\s*[、.．]?(.+)$/);
        if (m) {
            const num = m[1].replace(/—/g, '-');
            const core = m[2].replace(/\.[^.]+$/, '').trim();
            if (core) {
                const cands = files.filter(p => {
                    const s2 = base(p).replace(/\.[^.]+$/, '');
                    return s2.startsWith(num) && s2.includes(core);
                });
                if (cands.length) {
                    // 同名编号常有 docx/pdf 双版本：优先与依据里相同扩展名
                    const ext = (name.match(/\.([^.]+)$/) || [])[1];
                    hit = (ext && cands.find(p => p.toLowerCase().endsWith('.' + ext.toLowerCase()))) || cands[0];
                }
            }
        }
    }
    if (!hit) {
        // ⑤ 任意位置编号：“承诺函（12-1）”“14-1号文件《不动产权证书》”→ 抽编号+核心词
        const nm = name.replace(/^[号文件No\.\s]+/, '');
        const numM = nm.match(/(\d+(?:[-—]\d+)?)/);
        if (numM) {
            const num = numM[1].replace(/—/g, '-');
            const core = nm.replace(numM[0], '').replace(/[（(][^）)]*[）)]/g, '')
                .replace(/[及与等表和号、,，\s]/g, '').replace(/\.[^.]+$/, '').trim();
            const cands = files.filter(p => {
                const s2 = base(p).replace(/\.[^.]+$/, '');
                return s2.includes(num) && (!core || s2.includes(core));
            });
            if (cands.length) {
                const ext = (name.match(/\.([^.]+)$/) || [])[1];
                hit = (ext && cands.find(p => p.toLowerCase().endsWith('.' + ext.toLowerCase()))) || cands[0];
            } else if (core.length >= 3) {
                hit = files.find(p => base(p).replace(/\.[^.]+$/, '').includes(core));
            }
        }
    }
    if (!hit) {
        // ⑥ 描述性依据：“各年度审计报告现金流量表”→ 拆词后取连续子串（长优先）去文件名里找
        const parts = name.replace(/\.[^.]+$/, '').split(/[及与、，,的下属]/)
            .map(s => s.trim()).filter(s => s.length >= 4);
        outer:
        for (const part of parts) {
            for (let L = Math.min(part.length, 10); L >= 4; L--) {
                for (let i = 0; i + L <= part.length; i++) {
                    const sub = part.slice(i, i + L);
                    const f = files.find(p => base(p).replace(/\.[^.]+$/, '').includes(sub));
                    if (f) { hit = f; break outer; }
                }
            }
        }
    }
    if (!hit) {
        // ⑧ 文件夹级依据：“26 润泽发展所获荣誉及奖项”“20号专项税务意见”是目录名 → 打开目录下第一个文件
        const nm2 = name.replace(/^[号文件No\.\s]+/, '').trim();
        if (nm2.length >= 4) {
            const dirs = [...new Set(files.map(p => p.split('/').slice(0, -1).join('/')))];
            const d = dirs.find(x => {
                const dn = x.split('/').pop();
                return dn === nm2 || dn.includes(nm2) || nm2.includes(dn);
            });
            if (d) {
                const under = files.filter(p => p.startsWith(d + '/')).sort();
                if (under.length) hit = under[0];
            }
        }
    }
    if (!hit) {
        // ⑦ 多文件混写：“法律意见书、不动产权证书…、估价报告…”→ 拆开逐项递归试
        for (const part of name.split(/[、]|及|，|,/).map(s => s.trim()).filter(s => s.length >= 4)) {
            if (part === name) continue;
            hit = await _findMaterialPath(part);
            if (hit) break;
        }
    }
    return hit || null;
}

/** 依据里的路径可能缺失或与磁盘不一致（长目录名被截断等）：先按原路径试，找不到再按文件名模糊匹配 */
async function openMaterialPreviewResolved(path, quote, page) {
    try {
        const files = await _getMaterialFiles();
        if (files.includes(path)) { openMaterialPreview(path, quote, page); return; }
        const real = await _findMaterialPath(path);
        if (real) { openMaterialPreview(real, quote, page); return; }
    } catch (e) { /* 清单拉取失败则按原路径尝试 */ }
    openMaterialPreview(path, quote, page);
}

/** 当前项目名（用于提示文案；拉不到则空） */
async function _curProjectName() {
    try {
        const ps = await API.listProjects();
        const p = (ps || []).find(x => String(x.id) === String(currentProjectId));
        return p ? p.name : '';
    } catch (e) { return ''; }
}

/** 依据文件找不到时的统一提示：说清“哪个项目、缺什么、怎么办” */
async function _showMissingMaterialTip(fileName) {
    const proj = await _curProjectName();
    const head = proj ? `当前项目“${proj}”的材料库中没有《${fileName}》` : `未在当前项目材料库中找到《${fileName}》`;
    showToast(`${head}——依据里的文件需在当前项目的“申报材料”里上传后才能回查原文`, 'warning');
}

/** 点“参考材料”清单项（只有文件名）：按名定位后打开原文预览。
 * 依据可能把多个文件用“、”混写在一起（还夹带“待人工核对”之类的提示），
 * 拆开逐个尝试，打开第一个能定位到的真实文件。 */
async function openRefByName(text) {
    const parts = String(text || '').split(/[、，,]/)
        .map(s => s.trim())
        .filter(s => s && !/待(人工)?核对|待补充|待确认/.test(s));
    for (const part of parts) {
        try {
            const path = await _findMaterialPath(part);
            if (path) {
                if (parts.length > 1) showToast(`依据提及多个文件，已打开第一个可定位的：《${path.split('/').pop()}》`);
                openMaterialPreview(path, ''); return;
            }
        } catch (e) { /* 材料列表拉取失败则继续试下一项 */ }
    }
    _showMissingMaterialTip(parts[0] || String(text || '').replace(/^📄\s*/, '').replace(/[《》]/g, '').split('/').pop() || '该文件');
}

/** 解析一条依据标注并跳转：申报材料→预览原文并高亮摘录；同上文件→复用上一条；摘要表→定位字段行 */
let _lastSrcMaterialPath = '';
/** 从依据正文里切出“文件路径 + 摘录 + 页码”。兼容两种格式：
 * ① 路径〈原文摘录〉摘录内容；② 路径〈摘录内容〉（无标记词，第一个〈…〉即摘录）。
 * 摘录末尾若标了“（第X页）”（生成时对扫描件的页码引注），解析出来直接跳页。 */
function _splitPathQuote(body) {
    let path, quote;
    const pm = body.split('〈原文摘录〉');
    if (pm.length > 1) {
        path = pm[0]; quote = pm.slice(1).join('〈原文摘录〉');
    } else {
        const m = body.match(/^([^〈]*)〈([^〉]*)〉/);
        if (m) { path = m[1]; quote = m[2]; }
        else { path = body; quote = ''; }
    }
    path = path.trim().replace(/^《(.+)》$/, '$1').replace(/〈[^〉]*〉.*$/, '').trim();
    quote = quote.replace(/[；;]\s*$/, '').trim();
    let page = 0;
    const pg = quote.match(/[（(]\s*第\s*(\d+)\s*页\s*[）)]\s*$/);
    if (pg) { page = parseInt(pg[1], 10) || 0; quote = quote.slice(0, pg.index).trim(); }
    return { path, quote, page };
}
async function openSrcLink(rawText, ctx) {
    const text = String(rawText || '').replace(/^📎\s*依据[：:]/, '').trim();
    if (!text) return;
    const ctxText = String(ctx || '').replace(/\s+/g, ' ').trim();
    // 先按〈数字〉拆出多条依据（〈1〉…〈2〉…）；再只在真条目边界拆（“；”后跟来源前缀/引注号）——
    // 摘录内容里的分号（“经营异常0条；司法判决0条”）不是边界，在那里拆会把路径/摘录切碎
    const items = [];
    for (const part of text.split(/〈\d+〉/)) {
        for (const seg of part.split(/；(?=(?:申报材料|摘要表|释义|其他基本信息|天眼查|网络公开信息|planning|固定表述|同上|待核实|〈\d+〉))/)
            .map(s => s.trim()).filter(Boolean)) items.push(seg);
    }
    for (const seg of items) {
        let m;
        if ((m = seg.match(/^申报材料[：:](.+)$/))) {
            const { path, quote, page } = _splitPathQuote(m[1].trim());
            // 依据没带摘录时，用依据上方的正文（表格/段落）当搜索文本，也能翻到页+红框
            if (path) { _lastSrcMaterialPath = path; openMaterialPreviewResolved(path, quote || (page ? '' : ctxText), page); return; }
        }
        if (/^同上文件/.test(seg)) {
            const { quote, page } = _splitPathQuote(seg);
            if (_lastSrcMaterialPath) { openMaterialPreviewResolved(_lastSrcMaterialPath, quote || (page ? '' : ctxText), page); return; }
            showToast('未能定位“同上文件”所指的上一条依据', 'warning'); return;
        }
        if ((m = seg.match(/^天眼查(?:查询)?[：:](.+)$/))) {
            // 括号里是查询结果摘要（如“对外投资企业分布城市包括…”），企业名只取括号前
            const company = m[1].trim().replace(/[（(][^）)]*[）)]\s*$/, '').trim();
            if (company) { openTianyancha(company); return; }
        }
        if ((m = seg.match(/^摘要表[：:](.+)$/))) { jumpToSummaryField(m[1].trim()); return; }
        if ((m = seg.match(/^planning\.md[：:](.+)$/)) || seg === 'planning.md') {
            // 依据来自写作总纲：打开 planning.md 原文并高亮摘录（冒号后是摘录内容）
            openMaterialPreview('planning.md', m ? m[1].trim() : '');
            return;
        }
        if (seg === '释义' || seg === '其他基本信息') {
            // 依据来自已保存的摘要数据：跳到摘要页查看
            await selectSummary();
            showToast(`该依据来自您已核对保存的“${seg}”，请在摘要页查看`);
            return;
        }
    }
    // 网络信息类依据：直接跳网页搜索查看出处（要言有据，不给死胡同）
    if (/网络公开信息|网络信息|网站|网页|官网/.test(text)) {
        const q = text.replace(/^网络公开信息[：:]/, '').trim() || text;
        window.open('https://www.baidu.com/s?wd=' + encodeURIComponent(q), '_blank');
        showToast('已跳转网页搜索查看该依据出处');
        return;
    }
    // 固定表述类依据：原文即系统内置模板套话，依据内容本身就是原文
    if (/^固定表述/.test(text)) {
        showToast('该依据为系统内置的固定表述模板，展示内容即模板原文');
        return;
    }
    // 通用来源召回：“实际/预测数据来源：备考财务报表（文号）；…估价报告（文号）”这类子句，
    // 剥“来源：”前缀后按文件名/文号模糊匹配材料库，命中即跳原文（子句当摘录搜页+红框）
    const clauses = text.split(/[；;]/).map(s => s.trim()).filter(Boolean);
    for (const c of clauses) {
        const name = c.replace(/^(实际|预测|估算|历史|财务)?(数据)?来源[：:]\s*/, '').trim();
        if (name.length < 4) continue;
        try {
            const p = await _findMaterialPath(name);
            if (p) {
                if (clauses.length > 1) showToast(`依据涉及多个来源，已打开首个可定位的：《${p.split('/').pop()}》`);
                openMaterialPreviewResolved(p, c, 0);
                return;
            }
        } catch (e) { /* 材料清单拉取失败则继续试下一子句 */ }
    }
    // 最终兜底：按名召回材料（裸文件名、多文件混写等旧格式）；
    // 召回失败时 openRefByName 会给出“材料库缺该文件、上传后可回查”的行动指引，不再说“无法定位”
    openRefByName(text);
}

/** 天眼查依据→官网：点击上下文里先开搜索页（不会被浏览器拦截），
 *  后台再调 MCP 解析精确企业 ID，解析到就把该标签页转去公司详情页；
 *  解析不到/失败则留在搜索页（首条即目标企业）。 */
function openTianyancha(company) {
    const searchUrl = 'https://www.tianyancha.com/search?key=' + encodeURIComponent(company);
    const win = window.open(searchUrl, '_blank');
    showToast(`已跳转天眼查查看“${company}”`);
    API.tianyanchaUrl(company).then(d => {
        if (win && d && d.url && d.url !== searchUrl) {
            try { win.location.href = d.url; } catch (e) { /* 跳转失败则保持搜索页 */ }
        }
    }).catch(() => { });
}

/** 材料原文预览弹窗：PDF 默认按页原版图（仿 Word/WPS 观感，无限滚动懒加载），
 *  其他格式/切换后走文本版（高亮“依据”摘录并滚动到该处） */
let _matState = null;  // {path, quote, page, mode:'pages'|'text', pagesState}
async function openMaterialPreview(path, quote, page) {
    let modal = document.getElementById('matPreviewModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'matPreviewModal';
        modal.className = 'mat-preview-overlay';
        modal.innerHTML = `
            <div class="mat-resize" id="matResize" title="拖拽调整宽度"></div>
            <div class="mat-preview-box">
                <div class="mat-preview-head">
                    <span class="mat-preview-title" id="matPreviewTitle">正在加载材料…</span>
                    <span style="display:flex;gap:8px">
                        <button class="btn btn-ghost btn-sm" id="matPreviewToggle" style="display:none"></button>
                        <button class="btn btn-ghost btn-sm" onclick="closeMaterialPreview()">✕ 关闭</button>
                    </span>
                </div>
                <div class="mat-preview-body" id="matPreviewBody"></div>
            </div>`;
        modal.addEventListener('click', (e) => { if (e.target === modal) closeMaterialPreview(); });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && modal.style.display === 'flex') closeMaterialPreview();
        });
        document.body.appendChild(modal);
        _initMatResize(modal);
    }
    if (_matDrawerW) document.documentElement.style.setProperty('--drawer-w', _matDrawerW + 'px');
    modal.style.display = 'flex';
    document.body.classList.add('mat-drawer-open');  // 主内容区让位，左右并列各自滚动
    _matState = { path, quote, page: page || 0, mode: /\.pdf$/i.test(path || '') ? 'pages' : 'text', pagesState: null };
    const toggle = document.getElementById('matPreviewToggle');
    if (toggle) {
        toggle.onclick = () => {
            _matState.mode = _matState.mode === 'pages' ? 'text' : 'pages';
            _matState.pagesState = null;
            _updateMatToggleBtn();
            if (_matState.mode === 'pages') _renderPagesPreview(); else _renderTextPreview();
        };
    }
    _updateMatToggleBtn();
    if (_matState.mode === 'pages') await _renderPagesPreview();
    else await _renderTextPreview();
}

function _updateMatToggleBtn() {
    const btn = document.getElementById('matPreviewToggle');
    if (!btn || !_matState) return;
    const isPdf = /\.pdf$/i.test(_matState.path || '');
    btn.style.display = isPdf ? '' : 'none';
    btn.innerHTML = _matState.mode === 'pages' ? ICN.edit + ' 文本版' : ICN.file + ' 原版页面';
}

/** PDF 按页原版图预览：首批 3 页；摘录命中页不在首批时从命中页开始，顶部提供“加载前几页” */
async function _renderPagesPreview() {
    const { path, quote } = _matState;
    const citedPageHint = Number(_matState.page || 0);
    const body = document.getElementById('matPreviewBody');
    document.getElementById('matPreviewTitle').textContent = '正在渲染原版页面…';
    body.innerHTML = '<div class="text-muted" style="padding:20px">正在渲染 PDF 原版页面，请稍候…</div>';
    let d;
    try {
        d = await API.previewMaterialPages(path, 1, 3, quote, 0, '', citedPageHint);
    } catch (e) {
        // 渲染失败（如加密 PDF）回退文本版
        _matState.mode = 'text'; _updateMatToggleBtn();
        return _renderTextPreview();
    }
    const citedPage = citedPageHint > 0 ? Math.max(1, Math.min(citedPageHint, d.total)) : 0;
    const targetPage = citedPage || d.hit_page || 0;
    const st = _matState.pagesState = { total: d.total, start: 1, end: 0, loading: false,
        hit: targetPage,
        hit_box: d.hit_page === targetPage ? (d.hit_box || null) : null,
        fuzzy: citedPage ? false : !!d.fuzzy,
        weak: citedPage ? false : !!d.weak };
    document.getElementById('matPreviewTitle').textContent = `📄 《${String(path).split('/').pop()}》原版（共 ${d.total} 页）`;
    body.innerHTML = '';
    if (citedPage > 0) {
        // 结构化依据里的物理页码优先；全文模糊搜索不得覆盖该页。
        body.insertAdjacentHTML('beforeend', `<div class="src-tip ok">✅ 依据标注摘录位于原文第 ${citedPage} 页，已为您跳转到该页。</div>`);
    }
    if (quote && !citedPage) {
        if (st.hit && st.weak) {
            if (d.hit_box) st.hit_box = d.hit_box;
            body.insertAdjacentHTML('beforeend', d.hit_box
                ? `<div class="src-tip ok">📖 摘录为概括性表述，该文件中无逐字对应原文，已为您跳到最相关的第 ${st.hit} 页并框出最相关段落，请核对。</div>`
                : `<div class="src-tip ok">📖 摘录为概括性表述，该文件中无逐字对应原文，已为您跳到最相关的第 ${st.hit} 页，请核对。</div>`);
        } else if (st.hit && d.hit_box) {
            st.hit_box = d.hit_box;
            body.insertAdjacentHTML('beforeend', st.fuzzy
                ? `<div class="src-tip ok">📍 摘录与原文文字略有出入，已为您跳到最相近的第 ${st.hit} 页并框出大致位置，请核对。</div>`
                : `<div class="src-tip ok">✅ 摘录位于原文第 ${st.hit} 页，已为您跳转到该页并红框标出摘录位置。</div>`);
        } else if (st.hit) {
            body.insertAdjacentHTML('beforeend', st.fuzzy
                ? `<div class="src-tip ok">📍 摘录与原文文字略有出入，已为您翻到最相近的第 ${st.hit} 页，上下滚动核对。</div>`
                : `<div class="src-tip ok">✅ 摘录位于原文第 ${st.hit} 页，已为您翻到该页附近，上下滚动可查看其他页。</div>`);
        } else if (d.has_text) {
            body.insertAdjacentHTML('beforeend', `<div class="src-tip warn">⚠️ 摘录未能在本文档中逐字定位（可能略有出入），摘录内容：“${_escHtmlAttr(quote)}”，请翻页核对。</div>`);
        } else {
            // 扫描件：后台免费本地 OCR 逐页搜索摘录所在页，轮询到结果自动跳页
            body.insertAdjacentHTML('beforeend', `<div class="src-tip ok" id="quoteSearchTip">🔍 正在逐页识别搜索摘录所在页（免费本地识别，一般几秒到几十秒），找到后自动跳转…</div>`);
            _startQuoteSearch();
        }
    }
    body.insertAdjacentHTML('beforeend', '<div id="pdfPrevBtnWrap"></div><div id="pdfPagesWrap"></div><div id="pdfLoadMore" class="text-muted" style="text-align:center;padding:12px;font-size:12px"></div>');
    if (st.hit && (st.hit > 2 || st.hit_box)) { st.start = st.hit; st.end = st.hit - 1; }
    _renderPrevBtn();
    await _appendPages();
    body.onscroll = () => {
        if (_matState && _matState.mode === 'pages' && body.scrollTop + body.clientHeight > body.scrollHeight - 600) _appendPages();
    };
}

function _renderPrevBtn() {
    const wrap = document.getElementById('pdfPrevBtnWrap');
    const st = _matState && _matState.pagesState;
    if (!wrap || !st) return;
    wrap.innerHTML = st.start > 1
        ? `<div style="text-align:center;padding:8px"><button class="btn btn-ghost btn-sm" onclick="_prependPages()">${ICN.upload} 加载第 ${Math.max(1, st.start - 3)}–${st.start - 1} 页</button></div>`
        : '';
}

function _pdfPageHtml(p, total) {
    return `<div class="pdf-page"><img src="data:image/jpeg;base64,${p.img}" alt="第 ${p.page} 页"><div class="pdf-page-no">— 第 ${p.page} 页 / 共 ${total} 页 —</div></div>`;
}

async function _appendPages() {
    const st = _matState && _matState.pagesState;
    if (!st || st.loading || st.end >= st.total) return;
    st.loading = true;
    const mySeq = st.seq = (st.seq || 0) + 1;
    const more = document.getElementById('pdfLoadMore');
    if (more) more.textContent = '正在加载…';
    const alive = () => (_matState && _matState.pagesState) === st && st.seq === mySeq;
    try {
        const withHl = st.hit && st.hit_box && st.hit >= st.end + 1 && st.hit <= st.end + 3;
        const d = await API.previewMaterialPages(_matState.path, st.end + 1, 3, '',
            withHl ? st.hit : 0, withHl ? st.hit_box.join(',') : '');
        if (!alive()) return;  // 视图已切换/搜页已跳转：过期响应丢弃，避免无红框图覆盖红框图
        const wrap = document.getElementById('pdfPagesWrap');
        for (const p of d.pages) wrap.insertAdjacentHTML('beforeend', _pdfPageHtml(p, st.total));
        st.end = Math.min(st.total, st.end + d.pages.length);
        if (more) more.textContent = st.end >= st.total ? '— 已到最后 —' : '↓ 向下滚动加载更多页';
    } catch (e) {
        if (alive() && more) more.textContent = '加载失败，滚动重试';
    } finally { if (alive()) st.loading = false; }
}

async function _prependPages() {
    const st = _matState && _matState.pagesState;
    if (!st || st.loading || st.start <= 1) return;
    st.loading = true;
    const mySeq = st.seq = (st.seq || 0) + 1;
    const alive = () => (_matState && _matState.pagesState) === st && st.seq === mySeq;
    try {
        const s = Math.max(1, st.start - 3);
        const withHl = st.hit && st.hit_box && st.hit >= s && st.hit < st.start;
        const d = await API.previewMaterialPages(_matState.path, s, st.start - s, '',
            withHl ? st.hit : 0, withHl ? st.hit_box.join(',') : '');
        if (!alive()) return;
        const wrap = document.getElementById('pdfPagesWrap');
        wrap.insertAdjacentHTML('afterbegin', d.pages.map(p => _pdfPageHtml(p, st.total)).join(''));
        st.start = s;
        _renderPrevBtn();
    } finally { if (alive()) st.loading = false; }
}

/** 扫描件后台搜页：轮询任务结果，命中后自动跳到该页 */
async function _startQuoteSearch() {
    const { path, quote } = _matState;
    try {
        const r0 = await API.quoteSearch(path, quote);
        if (r0.status === 'done' && r0.hit) return _onQuoteHit(r0.hit, r0.box, r0.fuzzy, r0.weak);
        for (let i = 0; i < 90; i++) {
            await new Promise(r => setTimeout(r, 2000));
            if (!_matState || _matState.mode !== 'pages') return;  // 弹窗已关/已切文本版
            const r = await API.quoteSearchResult(r0.task);
            const tip = document.getElementById('quoteSearchTip');
            if (r.status === 'done') {
                if (r.hit) return _onQuoteHit(r.hit, r.box, r.fuzzy, r.weak);
                if (tip) tip.className = 'src-tip warn';
                if (tip) tip.innerHTML = `⚠️ 已逐页识别全文但未找到摘录所在页（识别文字可能出入较大），摘录内容：“${_escHtmlAttr(quote)}”，请翻页核对。`;
                return;
            }
            if (tip) tip.innerHTML = `🔍 正在逐页识别搜索摘录所在页（免费本地识别）：已扫到第 ${r.scanned || '…'} 页，找到后自动跳转…`;
        }
    } catch (e) { /* 搜页失败不影响翻页浏览 */ }
}

function _onQuoteHit(hit, box, fuzzy, weak) {
    const tip = document.getElementById('quoteSearchTip');
    if (tip) tip.innerHTML = weak
        ? `📖 摘录为概括性表述，该文件中无逐字对应原文，已为您跳到最相关的第 ${hit} 页${box ? '并框出最相关段落' : ''}，请核对。`
        : fuzzy
            ? `📍 摘录与识别文字略有出入，已为您跳到最相近的第 ${hit} 页${box ? '并框出大致位置' : ''}，请核对。`
            : `✅ 摘录位于原文第 ${hit} 页，已为您跳转到该页${box ? '并红框标出摘录位置' : '，请上下滚动核对摘录位置'}。`;
    const st = _matState && _matState.pagesState;
    if (!st) return;
    // 作废在途的无红框渲染并释放加载锁，确保下面的带红框请求一定发出
    st.seq = (st.seq || 0) + 1;
    st.loading = false;
    st.start = hit; st.end = hit - 1; st.hit = hit; st.hit_box = box || null;
    const wrap = document.getElementById('pdfPagesWrap');
    if (wrap) wrap.innerHTML = '';
    _renderPrevBtn();
    _appendPages();
}

/** 文本版预览：加载解析文本，高亮“依据”里摘录的原句并滚动到该处 */
async function _renderTextPreview() {
    const { path, quote } = _matState;
    const body = document.getElementById('matPreviewBody');
    body.onscroll = null;
    document.getElementById('matPreviewTitle').textContent = '正在加载材料原文…';
    body.innerHTML = '<div class="text-muted" style="padding:20px">正在解析材料，请稍候…（扫描件需要识别文字，会稍慢）</div>';
    try {
        const d = await API.previewMaterial(path);
        document.getElementById('matPreviewTitle').textContent = `📄 《${d.filename}》原文`;
        const body = document.getElementById('matPreviewBody');
        const text = d.text || '（未能解析出该文件的文字）';
        // 定位摘录：①逐字匹配；②开头段兜底；③OCR/扫描件文本常夹杂空格换行，
        // 用“去空白映射表”做忽略空格匹配；④再不行把常见标点差异也抹平后匹配。
        let idx = -1, markLen = 0;
        if (quote) {
            idx = text.indexOf(quote); markLen = quote.length;
            if (idx < 0) {
                const head = quote.slice(0, Math.max(6, Math.min(14, quote.length)));
                idx = text.indexOf(head); markLen = head.length;
            }
            if (idx < 0) {
                // 忽略所有空白字符的匹配（OCR 输出常在汉字间夹杂空格/换行）
                const q = quote.replace(/\s+/g, '');
                const map = [];  // 去空白文本下标 → 原文下标
                let norm = '';
                for (let i = 0; i < text.length; i++) {
                    const ch = text[i];
                    if (!/\s/.test(ch)) { map.push(i); norm += ch; }
                }
                let ni = norm.indexOf(q);
                let matchChars = q.length;
                if (ni < 0) {
                    // 标点归一后再试（OCR 常把“”识成""、全角半角互差）
                    const flat = s => s.replace(/[“”]/g, '"').replace(/[‘’]/g, "'")
                        .replace(/（/g, '(').replace(/）/g, ')').replace(/[，。；：、]/g, '');
                    const q2 = flat(q), n2 = flat(norm);
                    ni = n2.indexOf(q2); matchChars = q2.length;
                }
                if (ni >= 0) {
                    idx = map[ni];
                    const end = map[ni + matchChars - 1];
                    markLen = (end !== undefined ? end : idx) - idx + 1;
                }
            }
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
        const proj = await _curProjectName();
        const fname = String(path || '').split('/').pop() || '该文件';
        const hint = proj
            ? `当前项目“${proj}”的材料库里没有《${fname}》。若该文件在压缩包其他章节目录里，请到“申报材料”补传对应文件夹后重试；若确未上传，需先上传才能回查原文。`
            : `材料库里没有《${fname}》，请先在“申报材料”里上传对应文件后重试。`;
        document.getElementById('matPreviewBody').innerHTML =
            `<div style="padding:20px;color:var(--danger);line-height:1.7">${_escHtmlAttr(hint)}</div>`;
    }
}

function closeMaterialPreview() {
    const modal = document.getElementById('matPreviewModal');
    if (modal) modal.style.display = 'none';
    document.body.classList.remove('mat-drawer-open');
}

/** 依据预览抽屉左缘拖拽调宽：--drawer-w 同时控制抽屉宽与主内容让位，改一处两侧联动 */
let _matDrawerW = null;
function _initMatResize(modal) {
    const handle = modal.querySelector('#matResize');
    if (!handle) return;
    let dragging = false;
    handle.addEventListener('mousedown', (e) => {
        dragging = true;
        document.body.classList.add('mat-resizing');
        e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const right = modal.getBoundingClientRect().right;
        let w = Math.round(right - e.clientX);
        w = Math.max(320, Math.min(w, Math.round(window.innerWidth * 0.85)));
        _matDrawerW = w;
        document.documentElement.style.setProperty('--drawer-w', w + 'px');
    });
    document.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false;
        document.body.classList.remove('mat-resizing');
    });
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
function _srcContext(el) {
    // 依据行上方的正文（表格/段落）：依据没带摘录时当搜索文本用
    const cands = [];
    if (el.previousElementSibling) cands.push(el.previousElementSibling);
    if (el.parentElement && el.parentElement.previousElementSibling) cands.push(el.parentElement.previousElementSibling);
    return cands.map(x => (x.textContent || '')).join(' ').replace(/\s+/g, ' ').trim().slice(0, 400);
}
document.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('.src-item-plain')) return;  // 不可溯源依据（固定表述等）不可点
    const item = e.target.closest && e.target.closest('.src-item');
    if (item) { openSrcLink(item.textContent, _srcContext(item)); return; }   // 逐句引注：点哪条跳哪条
    const srcEl = e.target.closest && e.target.closest('.doc-src');
    if (srcEl) {
        if (srcEl.classList.contains('doc-src-plain')) return;  // 整行不可溯源依据不可点
        openSrcLink(srcEl.textContent, _srcContext(srcEl)); return;
    }
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
        ensure(segs).files.push({ name: fname, size: f.size, path: f.path });
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
                <span class="mc-ico">${ICN.folder}</span><span class="mc-name" title="${_escHtmlAttr(d)}">${_escHtmlAttr(d)}</span>
                <span class="mc-meta">${_countTreeFiles(node.dirs.get(d))}</span><span class="mc-arrow">▸</span></div>`;
        }
        for (const f of node.files.sort((a, b) => a.name.localeCompare(b.name, 'zh'))) {
            h += `<div class="mc-row mc-file" data-name="${_escHtmlAttr(f.name)}" data-path="${_escHtmlAttr(f.path)}" data-type="file" title="点击查看原件：${_escHtmlAttr(f.name)}">
                <span class="mc-ico">📄</span><span class="mc-name">${_escHtmlAttr(f.name)}</span>
                <span class="mc-meta">${_fmtSize(f.size)}</span><span class="mc-open-hint">查看</span></div>`;
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
            } else if (row.dataset.type === 'file' && row.dataset.path) {
                openMaterialPreview(row.dataset.path, '', 0);
            }
        });
        return col;
    };

    const first = makeCol(root, 0);
    cols.push(first);
    container.appendChild(first);
}

/** 缺件体检提示：catalog_check 有必交项缺失时，在统计行下方插黄色提醒条（业务语言）；
 * available=false 或无缺件时移除提示。 */
function _setCatalogWarn(hostId, cc) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const elId = hostId + '-warn';
    let el = document.getElementById(elId);
    const miss = (cc && cc.available && Array.isArray(cc.missing)) ? cc.missing : [];
    if (!miss.length) { if (el) el.remove(); return; }
    if (!el) {
        el = document.createElement('div');
        el.id = elId;
        el.style.cssText = 'margin:6px 0;padding:8px 10px;border-radius:6px;background:#fff7e6;border:1px solid #ffd591;color:#874d00;font-size:13px;line-height:1.6;';
        host.insertAdjacentElement('afterend', el);
    }
    const items = miss.slice(0, 8).map(m => `第${m.no}项·${m.name}`).join('；');
    const more = miss.length > 8 ? `（等共${miss.length}项）` : '';
    el.textContent = `⚠ 缺少${miss.length}项材料：${items}${more}。建议补充后再发起章节生成。`;
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
        // 25 项清单缺件提示（后端 catalog_check；不可用/无缺件时自动移除）
        _setCatalogWarn('materialsStat', data.catalog_check);
        _setCatalogWarn('matPanelStat', data.catalog_check);
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
        // 补传场景：先拉现有清单，同路径同大小的文件直接跳过（不重复传输），只补缺失的
        let arr = Array.from(files);
        let preSkipped = 0;
        try {
            const d = await API.listMaterials();
            const existing = new Set(((d && d.files) || []).map(f => f.path + '|' + f.size));
            const before = arr.length;
            arr = arr.filter(f => !existing.has((f.webkitRelativePath || f.name) + '|' + f.size));
            preSkipped = before - arr.length;
        } catch (e) { /* 拉清单失败则全量上传，后端会按同名同大小兑底跳过 */ }
        if (!arr.length) {
            showToast(`所选 ${files.length} 个文件均已存在，无需重复上传`);
            return;
        }
        // 顶部状态与 toast 都说明白：共多少 / 已有多少 / 本次补多少
        const planTip = `共 ${files.length} 个文件 · 已有 ${preSkipped} 个 · 本次补传 ${arr.length} 个缺失文件`;
        if (stat) stat.textContent = planTip;
        if (pStat) pStat.textContent = planTip;
        if (preSkipped) showToast(planTip);
        // 按大小分批（单批 ≤40MB 且 ≤15 个），避免大批次超服务器请求上限整批失败
        const result = { uploaded: [], extracted_from_zip: 0, skipped: [], existed: [] };
        const batches = [];
        let i = 0;
        while (i < arr.length) {
            let j = i, size = 0;
            while (j < arr.length && (j === i || (size + (arr[j].size || 0) <= 40 * 1024 * 1024 && j - i < 15))) {
                size += arr[j].size || 0; j++;
            }
            batches.push(arr.slice(i, j));
            i = j;
        }
        let doneFiles = 0, batchNo = 0;
        for (const b of batches) {
            batchNo++;
            const tip = `补传中（第 ${batchNo}/${batches.length} 批）· 共 ${files.length} 个 · 已有 ${preSkipped} 个 · 已补 ${doneFiles}/${arr.length} 个`;
            if (stat) stat.textContent = tip;
            if (pStat) pStat.textContent = tip;
            const r = await API.uploadMaterials(b);
            result.uploaded = result.uploaded.concat(r.uploaded || []);
            result.extracted_from_zip += (r.extracted_from_zip || 0);
            result.skipped = result.skipped.concat(r.skipped || []);
            result.existed = result.existed.concat(r.existed || []);
            doneFiles += b.length;
        }
        _invalidateMatCache();
        const parts = [];
        if (result.uploaded && result.uploaded.length) parts.push(`新增 ${result.uploaded.length} 个`);
        if (result.extracted_from_zip) parts.push(`zip 解压出 ${result.extracted_from_zip} 个`);
        const existTotal = preSkipped + result.existed.length;
        if (existTotal) parts.push(`已存在自动跳过 ${existTotal} 个`);
        if (result.skipped && result.skipped.length) parts.push(`跳过不支持的格式 ${result.skipped.length} 个`);
        showToast('补传完成：' + (parts.join('，') || '无新增文件'));
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
                { icon: ICN.folder, value: projects.length, label: '项目总数', color: 'blue' },
                { icon: ICN.refresh, value: generating, label: '生成中', color: 'orange' },
                { icon: ICN.check, value: completed, label: '已完成', color: 'green' },
                { icon: ICN.grid, value: 1, label: '模板数', color: 'purple' },
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
 * 加载小节级生产线：进入项目后默认停在数据提取，不再打开旧的大章编辑器。
 */
async function loadChapters() {
    if (!currentProjectId) return;
    _setNdrcMode('generate');

    try {
        await loadProjectPack();
        await renderChapterStepper();
        _renderNdrcLandingState();
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
 * 渲染小节步骤条：按官方大章节折叠，默认只漏出章标题，点开展开该章全部二级小节
 * （31 节平铺太长，业务要求按章收起）。已配置 Know-how 的小节可点击生成/查看；
 * 未配置的显示"未配置"状态，点击后引导去 Know-how 页补充；报告审核固定在最后。
 */
async function renderChapterStepper() {
    const container = document.getElementById('chapterStepper');
    if (!container) return;
    let sections = [];
    try { sections = (await API.listAllSkillSections()).sections || []; } catch (_) {}
    const byChapter = {};
    sections.forEach(s => {
        (byChapter[s.chapter_n] ||= { title: s.chapter_title, items: [] }).items.push(s);
    });
    const chapterNs = Object.keys(byChapter).map(Number).sort((a, b) => a - b);
    container.innerHTML = chapterNs.map(n => {
        const g = byChapter[n];
        const configuredCount = g.items.filter(s => s.configured).length;
        const generatedCount = g.items.filter(s => s.configured && s.generated).length;
        const open = configuredCount > 0;  // 有已配置小节的章默认展开，方便直接看到演示内容
        const rows = g.items.map(s => {
            const done = s.configured && s.generated;
            const cls = !s.configured ? 'muted' : (done ? 'done' : '');
            const desc = !s.configured ? '未配置 Know-how'
                : (s.generated ? '已生成' : (s.data_ready ? '可生成' : '待提取数据'));
            const onClick = s.configured
                ? `selectSkillSection('${s.id}')`
                : `selectUnconfiguredSection('${s.id}', '${_escHtmlAttr(s.title)}')`;
            return `<div class="stepper-section-item ${cls}" onclick="${onClick}">
                <span class="stepper-section-dot"></span>
                <span class="stepper-section-title">${_escHtmlAttr(s.id)} ${_escHtmlAttr(s.title)}</span>
                <span class="stepper-section-desc">${_escHtmlAttr(desc)}</span>
            </div>`;
        }).join('');
        const batchLabel = generatedCount > 0 ? `重新生成本章（${configuredCount}节）` : `生成本章（${configuredCount}节）`;
        return `<details class="stepper-chapter" ${open ? 'open' : ''}>
            <summary><span class="stepper-chapter-name">第${n}章 · ${_escHtmlAttr(g.title)}</span>
                <span class="text-muted text-sm">（${configuredCount}/${g.items.length} 已配置）</span>
                <button id="chapterGenerateBtn${n}" type="button" class="btn btn-primary btn-sm stepper-chapter-generate"
                    ${configuredCount ? '' : 'disabled'}
                    title="${configuredCount ? '一次生成本章全部已配置小节；未配置小节自动跳过' : '本章还没有可执行的 Know-how'}"
                    onclick="event.preventDefault();event.stopPropagation();generateSkillChapterUI(${n},${generatedCount})">${configuredCount ? batchLabel : '暂无可生成小节'}</button>
            </summary>
            <div class="stepper-chapter-body">${rows}</div>
        </details>`;
    }).join('') + `<div class="stepper-section-item ${currentChapter === 'report-audit' ? 'done' : ''}" onclick="selectReportAudit()" style="margin-top:8px;border-top:1px solid var(--border);padding-top:12px">
        <span class="stepper-section-dot"></span>
        <span class="stepper-section-title">报告审核</span>
        <span class="stepper-section-desc">审核已生成小节</span>
    </div>`;
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
    if (circle && idx >= 0) circle.textContent = idx + 2;  // 第 1 项是数据提取，占位 1
    const desc = step.querySelector('.step-desc');
    if (desc) desc.textContent = '待生成';
}

/** 按 renderChapterStepper 的固定顺序定位第 n 章节点：位置 0 恒为“数据提取”，
 * 紧接着按 PACK_CHAPTERS 顺序铺开整章入口，之后才是小节/审核项——因此章节位置
 * 只取决于 PACK_CHAPTERS 中的下标，不受后面新增/减少多少小节级入口影响。 */
function _stepperStepFor(container, n) {
    const idx = (PACK_CHAPTERS || []).findIndex(ch => ch.n === n);
    if (idx < 0) return null;
    const steps = container.querySelectorAll('.step');
    return steps[1 + idx] || null;
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

// ===== 字段级数据底座与审核层 =====

let _foundationData = null;
let _foundationRules = null;
let _foundationDocuments = [];
let _extractPollTimer = null;

/** 申报材料页默认落地状态：只提示从左侧选小节，不再默认显示数据提取内容
 * ——数据提取现在是独立侧边栏页面，两处职责分开。 */
function _renderNdrcLandingState() {
    currentChapter = null;
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    container.innerHTML = `
        <div class="foundation-empty" style="padding:40px;text-align:center">
            <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">请从左侧选择一个小节</h3>
            <p class="text-muted text-sm" style="margin-top:8px">已配置 Know-how 的小节可以生成、查看结果和 Word 预览；数据提取和 Know-how 编辑分别在各自的侧边栏页面。</p>
        </div>`;
}

async function selectDataFoundation() {
    currentChapter = 'foundation';
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    container.innerHTML = `
        <div class="chapter-detail-header">
            <div>
                <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">数据提取</h3>
                <div class="text-muted text-sm" style="margin-top:4px">上传材料不会自动调用 AI；由业务确认后手动提取，规则和结果均可修改</div>
            </div>
            <button class="btn btn-primary" onclick="startFullDataExtraction(false)">提取数据</button>
        </div>
        <div class="data-workbench-tabs">
            <button class="active" data-tab="foundation" onclick="showDataWorkspaceTab('foundation')">1. 数据与规则</button>
            <button data-tab="manual" onclick="showDataWorkspaceTab('manual')">2. 人工输入</button>
            <button data-tab="documents" onclick="showDataWorkspaceTab('documents')">3. 底稿 Markdown</button>
        </div>
        <div class="chapter-detail-body" id="foundationBody"></div>`;
    await showDataWorkspaceTab('foundation');
}

async function showDataWorkspaceTab(tab) {
    document.querySelectorAll('.data-workbench-tabs button').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tab));
    const body = document.getElementById('foundationBody');
    if (!body) return;
    body.innerHTML = '<div class="text-sm text-muted" style="padding:16px 0">正在加载…</div>';
    if (tab === 'manual') return loadManualInputsWorkspace();
    if (tab === 'documents') return loadDocumentLibraryWorkspace();
    return loadFoundationWorkspace();
}

let _manualInputSources = [];

function openManualInputSource(index) {
    const source = _manualInputSources[index];
    if (source && source.path) openMaterialPreview(source.path, '');
}

async function loadManualInputsWorkspace(refresh = false) {
    const body = document.getElementById('foundationBody');
    try {
        const resp = refresh ? await API.refreshManualInputs() : await API.getManualInputs();
        const d = resp.data || {};
        _manualInputSources = d.sources || [];
        const renderRows = rows => `<table class="foundation-table"><thead><tr><th>字段名称</th><th>业务填写值</th></tr></thead><tbody>${(rows || []).map(r => `<tr><td><b>${_escHtmlAttr(r.label || '')}</b></td><td>${_escHtmlAttr(r.value || '').replace(/\n/g, '<br>')}</td></tr>`).join('')}</tbody></table>`;
        body.innerHTML = `
            <div class="workspace-toolbar"><div><b>人工输入层</b><small>当前仅接收两份业务手填 Word，不进入 AI 抽取字段库</small></div><button class="btn btn-ghost btn-sm" onclick="loadManualInputsWorkspace(true)">↻ 重新读取 Word</button></div>
            <div class="foundation-source-grid">${_manualInputSources.map((s, i) => `<div class="foundation-source ${s.status === 'located' ? 'located' : 'missing'}"><span class="source-dot"></span><div><b>${_escHtmlAttr(s.label)}</b><small>${_escHtmlAttr(s.path || '未找到文件')}</small></div>${s.path ? `<button class="btn btn-ghost btn-sm" onclick="openManualInputSource(${i})">查看原件</button>` : ''}</div>`).join('')}</div>
            <details class="foundation-panel" open><summary>摘要表（${(d.summary?.rows || []).length} 行）</summary><div class="foundation-table-wrap">${renderRows(d.summary?.rows)}</div></details>
            <details class="foundation-panel"><summary>项目概况表（${(d.project_overview?.rows || []).length} 行，生成第一章第一节时整表复制）</summary><div class="foundation-table-wrap">${renderRows(d.project_overview?.rows)}</div></details>`;
    } catch (e) {
        body.innerHTML = `<div class="foundation-error">人工输入读取失败：${_escHtmlAttr(e.message)}</div>`;
    }
}

let _documentLibrary = [];

async function loadDocumentLibraryWorkspace() {
    const body = document.getElementById('foundationBody');
    try {
        const resp = await API.getDocumentLibrary();
        _documentLibrary = resp.documents || [];
        const required = _documentLibrary.filter(d => d.required);
        const allWithIndex = _documentLibrary.map((d, i) => ({ d, i }))
            .sort((a, b) => Number(b.d.required) - Number(a.d.required) || a.d.path.localeCompare(b.d.path, 'zh-CN'));
        body.innerHTML = `
            <div class="workspace-toolbar"><div><b>一份底稿，一个 Markdown</b><small>全目录可见，Know-how 命中文件排在前面；目标页视觉精读会覆盖对应页 Markdown</small></div><div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="buildDocuments(false, false)">构建全部 Markdown</button><button class="btn btn-ghost btn-sm" onclick="buildDocuments(true, false)">构建目标底稿</button><button class="btn btn-primary btn-sm" onclick="buildDocuments(false, true)">本地 OCR 补全全部</button></div></div>
            <div class="foundation-flow"><span>材料总数 <b>${_documentLibrary.length}</b></span><i>→</i><span>当前规则命中 <b>${required.length}</b></span><i>→</i><span>已有 Markdown <b>${_documentLibrary.filter(d => ['ready','partial'].includes(d.status)).length}</b></span><i>→</i><span>待识别页 <b>${_documentLibrary.reduce((n,d) => n + (d.placeholder_pages || 0), 0)}</b></span></div>
            <input class="form-input" placeholder="搜索底稿文件名或路径…" oninput="filterDocumentLibrary(this.value)" style="max-width:360px;margin-bottom:10px">
            <div class="document-library-split">
                <div class="document-library-list" id="documentLibraryList">${allWithIndex.length ? allWithIndex.map(({ d, i }) => `<div class="document-library-row ${d.required ? 'required' : ''}" data-search="${_escHtmlAttr((d.filename + ' ' + d.path).toLowerCase())}">
                    <div class="doc-state ${d.status}">${d.status === 'ready' ? '✓' : (d.status === 'partial' ? '◐' : '·')}</div>
                    <div class="doc-main"><b>${d.required ? '<em class="required-mark">Know-how 命中</em>' : ''}${_escHtmlAttr(d.filename)}</b><small>${_escHtmlAttr(d.path)}</small><span>${d.page_count ? `${d.page_count} 页 · 原生文字 ${d.native_pages || 0} · OCR ${d.ocr_pages || 0} · 视觉 ${d.vision_pages || 0} · 待识别 ${d.placeholder_pages || 0}` : (d.status === 'pending' ? '尚未构建 Markdown' : '非分页文档')}</span></div>
                    <div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="viewDocumentMarkdown(${i})">查看 Markdown</button>${d.extension === '.pdf' ? `<button class="btn btn-ghost btn-sm" onclick="refineDocumentPagesUI(${i})">精读指定页</button>` : ''}</div>
                </div>`).join('') : '<div class="text-muted text-sm" style="padding:8px 0">当前没有可构建的底稿</div>'}</div>
                <div id="documentMarkdownViewer"></div>
            </div>`;
    } catch (e) {
        body.innerHTML = `<div class="foundation-error">底稿知识库加载失败：${_escHtmlAttr(e.message)}</div>`;
    }
}

function filterDocumentLibrary(keyword) {
    const value = String(keyword || '').trim().toLowerCase();
    document.querySelectorAll('#documentLibraryList .document-library-row').forEach(row => {
        row.style.display = !value || (row.dataset.search || '').includes(value) ? '' : 'none';
    });
}

async function buildDocuments(requiredOnly, fullOcr) {
    const body = document.getElementById('foundationBody');
    if (body) body.insertAdjacentHTML('afterbegin', `<div class="foundation-alert warn" id="documentBuilding">正在${fullOcr ? '逐页执行本地 OCR' : '读取原生文字层并建立 Markdown'}，请稍候…</div>`);
    try {
        const resp = await API.buildDocumentLibrary({ required_only: !!requiredOnly, full_ocr: !!fullOcr, force: false });
        showToast(`已处理 ${resp.processed || 0} 份${requiredOnly ? '目标' : '全部'}底稿${resp.errors?.length ? `，${resp.errors.length} 份失败` : ''}`);
        await loadDocumentLibraryWorkspace();
    } catch (e) {
        document.getElementById('documentBuilding')?.remove();
        showToast('底稿构建失败：' + e.message, 'error');
    }
}

async function viewDocumentMarkdown(index) {
    const doc = _documentLibrary[index], viewer = document.getElementById('documentMarkdownViewer');
    if (!doc || !viewer) return;
    viewer.innerHTML = '<div class="text-muted" style="padding:16px">正在读取 Markdown…</div>';
    try {
        const resp = await API.getDocumentMarkdown(doc.path);
        viewer.innerHTML = `<div class="markdown-viewer-head"><b>${_escHtmlAttr(doc.filename)}</b><button class="btn btn-ghost btn-sm" onclick="document.getElementById('documentMarkdownViewer').innerHTML=''">关闭</button></div><pre>${_escHtmlAttr(resp.markdown || '')}</pre>`;
        viewer.scrollIntoView({behavior:'smooth', block:'start'});
    } catch (e) { viewer.innerHTML = `<div class="foundation-error">读取失败：${_escHtmlAttr(e.message)}</div>`; }
}

async function refineDocumentPagesUI(index) {
    const doc = _documentLibrary[index];
    if (!doc) return;
    const raw = prompt(`请输入要精读的页码（例如 1,3-5）。该操作优先使用视觉模型，失败时回退本地 OCR。\n\n${doc.filename}`, '1');
    if (!raw) return;
    const pages = [];
    raw.split(/[,，\s]+/).forEach(part => {
        const m = part.match(/^(\d+)-(\d+)$/);
        if (m) for (let n = +m[1]; n <= +m[2]; n++) pages.push(n);
        else if (/^\d+$/.test(part)) pages.push(+part);
    });
    const instruction = prompt('业务特别关注什么字段或表格？可留空。', '') || '';
    try {
        const resp = await API.refineDocumentPages(doc.path, pages, instruction);
        showToast(`已精读 ${resp.refined?.length || 0} 页`);
        await loadDocumentLibraryWorkspace();
    } catch (e) { showToast('指定页精读失败：' + e.message, 'error'); }
}

async function loadFoundationWorkspace() {
    const body = document.getElementById('foundationBody');
    if (!body) return;
    body.innerHTML = '<div class="text-sm text-muted" style="padding:16px 0">正在加载数据中间层…</div>';
    try {
        const [resp, rulesResp, docsResp, job] = await Promise.all([
            API.getDataFoundation(), API.getDataFoundationRules(), API.getDocumentLibrary(), API.getDataExtractionStatus()
        ]);
        _foundationRules = rulesResp.data || null;
        _foundationDocuments = docsResp.documents || [];
        if (job.status === 'running') {
            renderExtractionProgress(job);
            pollDataExtraction();
            return;
        }
        if (!resp.exists) {
            body.innerHTML = `
                <div class="foundation-empty">
                    <h4>尚未提取数据</h4>
                    <p>上传材料本身不会触发 OCR、模型、天眼查或联网搜索。确认文件后再手动开始。</p>
                    <p class="text-muted text-sm">规则版本：${_escHtmlAttr(resp.rule_version || '未配置')}</p>
                    <button class="btn btn-primary" onclick="startFullDataExtraction(false)">提取数据</button>
                </div>`;
            return;
        }
        _foundationData = resp.data;
        renderDataFoundation();
    } catch (e) {
        body.innerHTML = `<div class="foundation-error">加载失败：${_escHtmlAttr(e.message)}</div>`;
    }
}

async function startFullDataExtraction(force) {
    try {
        await API.startDataExtraction(!!force);
        pollDataExtraction();
    } catch (e) { showToast('启动提取失败：' + e.message, 'error'); }
}

function renderExtractionProgress(job) {
    const body = document.getElementById('foundationBody');
    if (!body) return;
    body.innerHTML = `<div class="extraction-progress-card">
        <div class="extraction-progress-head"><b>正在提取数据</b><span>${job.percent || 0}%</span></div>
        <div class="progress-bar"><div class="progress-fill blue" style="width:${job.percent || 0}%"></div></div>
        <p>${_escHtmlAttr(job.message || '')}</p>
        <small>可离开本页面继续操作；返回后会恢复当前进度。天眼查和联网搜索会在最后阶段执行。</small>
    </div>`;
}

function pollDataExtraction() {
    if (_extractPollTimer) clearInterval(_extractPollTimer);
    const tick = async () => {
        try {
            const job = await API.getDataExtractionStatus();
            if (job.status === 'running') return renderExtractionProgress(job);
            clearInterval(_extractPollTimer); _extractPollTimer = null;
            if (job.status === 'done') {
                _foundationData = job.data;
                await renderChapterStepper();
                await loadFoundationWorkspace();
                showToast('数据提取完成；请检查字段和规则后按章批量生成或按小节精修');
            } else if (job.status === 'error') {
                const body = document.getElementById('foundationBody');
                if (body) body.innerHTML = `<div class="foundation-error">提取失败：${_escHtmlAttr(job.error || '')}</div><button class="btn btn-primary" onclick="startFullDataExtraction(true)">重新提取</button>`;
            }
        } catch (_) {}
    };
    tick();
    _extractPollTimer = setInterval(tick, 2500);
}

async function buildDataFoundation() {
    const body = document.getElementById('foundationBody');
    if (body) body.innerHTML = '<div class="text-muted" style="padding:18px">正在按业务 Know-how 刷新抽取规则、来源和字段…</div>';
    try {
        const resp = await API.buildDataFoundation();
        _foundationData = resp.data;
        renderDataFoundation();
        showToast('数据中间层已刷新；人工修订已保留，规则和来源可展开检查');
    } catch (e) {
        if (body) body.innerHTML = `<div class="foundation-error">构建失败：${_escHtmlAttr(e.message)}</div>`;
        showToast('构建失败：' + e.message, 'error');
    }
}

async function deepExtractDataFoundation() {
    if (!_foundationData) {
        await buildDataFoundation();
        if (!_foundationData) return;
    }
    const body = document.getElementById('foundationBody');
    if (body) body.insertAdjacentHTML('afterbegin', '<div class="foundation-alert warn" id="foundationExtracting">正在识别营业执照、承诺函、信用报告，并定位四期财报三张合并报表目标页；不会 OCR 全部附注，请稍候…</div>');
    try {
        const resp = await API.deepExtractDataFoundation();
        _foundationData = resp.data;
        renderDataFoundation();
        showToast('专项提取完成；目标页、抽取逻辑、当前值和冲突候选已更新');
    } catch (e) {
        const banner = document.getElementById('foundationExtracting');
        if (banner) banner.remove();
        showToast('专项提取失败：' + e.message, 'error');
    }
}

function _foundationStatus(status) {
    const map = {
        extracted: ['已抽取', 'ok'], manual: ['人工值', 'manual'],
        calculated: ['已计算', 'ok'], conflict: ['有冲突', 'warn'],
        missing: ['缺失', 'bad'], disabled: ['已删除', ''],
    };
    return map[status] || [status || '未知', ''];
}

function _foundationReview(status) {
    return status === 'approved' ? '已通过' : (status === 'rejected' ? '已退回' : '待审核');
}

function renderDataFoundation() {
    const body = document.getElementById('foundationBody');
    if (!body || !_foundationData) return;
    const d = _foundationData, st = d.stats || {};
    const stale = d.stale ? `<div class="foundation-alert warn">上传材料自上次构建后发生变化，请先点“构建 / 刷新底座”。</div>` : '';
    const missingSources = (d.sources || []).filter(s => s.required && s.status !== 'located');
    const sourceAlert = missingSources.length
        ? `<div class="foundation-alert bad">缺少必需来源：${missingSources.map(s => _escHtmlAttr(s.label)).join('、')}</div>` : '';
    body.innerHTML = `
        ${stale}${sourceAlert}
        <div class="workspace-toolbar"><div><b>数据中间层</b><small>材料选择和字段 Prompt 是同模板项目共用的 Know-how；具体文件、年份和页码是本项目运行结果</small></div><div class="flex gap-8"><button class="btn btn-primary btn-sm" onclick="startFullDataExtraction(true)">重新提取全部数据</button></div></div>
        <div class="foundation-flow">
            <span>① 业务 Know-how → 小节 Skill</span><i>→</i>
            <span>② 一份底稿一个 Markdown</span><i>→</i>
            <span>③ 按可编辑规则提取 <b>${st.field_filled || 0}/${st.field_total || 0}</b></span><i>→</i>
            <span>④ 业务修订 <b>${(_foundationData.fields || []).filter(f => f.is_override).length}</b></span><i>→</i>
            <span>已删除 <b>${st.disabled_total || 0}</b></span>
        </div>
        <div class="foundation-stat-grid">
            <div class="foundation-stat"><span>必填缺失</span><strong class="${st.required_missing ? 'danger' : ''}">${st.required_missing || 0}</strong></div>
            <div class="foundation-stat"><span>来源冲突</span><strong class="${(d.fields || []).some(f => f.status === 'conflict') ? 'danger' : ''}">${(d.fields || []).filter(f => f.status === 'conflict').length}</strong></div>
            <div class="foundation-stat"><span>目标页批次</span><strong>${(d.financial_extraction_runs || []).length}</strong></div>
            <div class="foundation-stat"><span>最近更新</span><strong class="date">${_escHtmlAttr((d.updated_at || d.built_at || '').replace('T', ' ').slice(0, 19))}</strong></div>
        </div>
        ${_renderFoundationValidations(d.input_validations || [])}
        ${_renderSourceRoleRules()}
        ${_renderFileReextractionRuns(d.file_reextraction_runs || [])}
        ${_renderFoundationFieldsTable()}
        `;
}

function buildRequiredDocuments(fullOcr) { return buildDocuments(true, fullOcr); }

function _csvRule(values) { return (values || []).join('、'); }

function _renderSourceRoleRules() {
    const roles = (_foundationRules?.source_roles || []).filter(r => r.input_kind !== 'manual_input' && r.selector);
    if (!roles.length) return '';
    return `<details class="foundation-panel"><summary>通用材料选择规则（${roles.length} 类）</summary>
        <div class="foundation-validations">${roles.map((role, index) => {
            const selector = role.selector || {};
            const runtimeMatches = (_foundationData?.source_selection_plan || []).filter(x =>
                x.role === role.id || (role.id === 'audit_reports' && String(x.role || '').startsWith('audit_report_')));
            const runtime = runtimeMatches[0] || {};
            const selectedText = runtimeMatches.length > 1
                ? runtimeMatches.map(x => `${x.label || x.role}：${x.selected_path || '未命中'}`).join('；')
                : (runtime.selected_path || '尚未命中');
            return `<div class="foundation-validation">
                <span>→</span><div style="width:100%"><b>${_escHtmlAttr(role.label || role.id)}</b>
                    <small>本项目命中：${_escHtmlAttr(selectedText)}</small>
                    <details class="foundation-rule-edit"><summary>✎ 编辑跨项目共用的文件匹配规则</summary><div class="rule-edit-form">
                        <label class="text-muted text-sm">文件名关键词（命中任一个）</label>
                        <input class="foundation-rule-input" id="sr-name-${index}" value="${_escHtmlAttr(_csvRule(selector.filename_keywords_any))}" placeholder="例：审计报告、财务报表">
                        <label class="text-muted text-sm">所在目录关键词（命中任一个）</label>
                        <input class="foundation-rule-input" id="sr-path-${index}" value="${_escHtmlAttr(_csvRule(selector.path_keywords_any))}" placeholder="例：原始权益人、参与主体">
                        <label class="text-muted text-sm">排除关键词</label>
                        <input class="foundation-rule-input" id="sr-exclude-${index}" value="${_escHtmlAttr(_csvRule(selector.exclude_keywords_any))}" placeholder="例：项目公司、备考报表">
                        <label class="text-muted text-sm">选文件说明（给 AI/业务看）</label>
                        <textarea class="foundation-rule-input" id="sr-prompt-${index}" rows="3">${_escHtmlAttr(role.match_prompt || '')}</textarea>
                        <button class="btn btn-primary btn-sm" onclick="saveSourceRoleRule(${index})">保存通用规则并在本项目重新匹配</button>
                    </div></details>
                    ${runtimeMatches.some(x => x.candidates?.length) ? `<details><summary>查看本次候选文件与得分</summary><pre>${_escHtmlAttr(JSON.stringify(runtimeMatches.map(x => ({period:x.label || x.role, candidates:x.candidates || []})), null, 2))}</pre></details>` : ''}
                </div><em>模板规则</em>
            </div>`;
        }).join('')}</div></details>`;
}

function _splitRuleKeywords(value) {
    return String(value || '').split(/[,，、;\n]+/).map(x => x.trim()).filter(Boolean);
}

async function saveSourceRoleRule(index) {
    const roles = (_foundationRules?.source_roles || []).filter(r => r.input_kind !== 'manual_input' && r.selector);
    const role = roles[index];
    if (!role) return;
    const selector = {...(role.selector || {})};
    selector.filename_keywords_any = _splitRuleKeywords(document.getElementById(`sr-name-${index}`)?.value);
    selector.path_keywords_any = _splitRuleKeywords(document.getElementById(`sr-path-${index}`)?.value);
    selector.exclude_keywords_any = _splitRuleKeywords(document.getElementById(`sr-exclude-${index}`)?.value);
    const match_prompt = document.getElementById(`sr-prompt-${index}`)?.value || '';
    if (!confirm('该修改会影响使用同一 Know-how 模板的所有项目。保存后在当前项目重新选文件并提取，继续？')) return;
    try {
        await API.updateDataFoundationRules([{entity:'source_role', id:role.id, selector, match_prompt}]);
        await startFullDataExtraction(true);
    } catch (e) { showToast('材料选择规则保存失败：' + e.message, 'error'); }
}


function _renderFoundationValidations(validations) {
    // 通过的校验不用列出来——这里只展示需要业务处理的失败项，通过项没有信息量。
    const failed = validations.filter(v => v.status === 'failed');
    if (!failed.length) return '';
    return `<details class="foundation-panel" open>
        <summary>一致性校验（${failed.length} 项需要处理）</summary>
        <div class="foundation-validations">${failed.map(v => `
            <div class="foundation-validation failed">
                <span>!</span>
                <div><b>${_escHtmlAttr(v.label)}</b><small>${_escHtmlAttr(v.message)}</small></div>
                <em>${v.severity === 'error' ? '阻断项' : '提示项'}</em>
            </div>`).join('')}</div>
    </details>`;
}

// 提取策略的机器标识翻译成业务能看懂的中文；来源角色的中文名从 _foundationRules.source_roles
// 取（rules.json 里业务已经写好的 label），外部/派生这几个不在那张表里，单独兜底。
const STRATEGY_LABELS = {
    table_exact: '按表格字段精确匹配', 'regex:': '从字段文本用规则提取子项',
    document_label: '文档正文字段定位', document_conclusion: '结论类专项提取（需人工复核）',
    document_list: '清单类专项提取（需人工复核）', financial_statement: '财务报表科目定位',
    document_search: '底稿全文检索', filename: '取自文件名', filename_title: '取自文件标题（自动去附件编号）', path_number: '取自材料编号',
    external_company_lookup: '天眼查企业查询', external_public_search: '公开网络检索',
    derived_analysis: '基于已提取数据计算', manual: '业务人员手工填写',
};
const ROLE_LABEL_FALLBACK = {
    tianyancha: '天眼查', web_search: '公开网络检索', derived: '数据中间层内部计算',
    project_materials: '项目申报材料（按角色未细分）', audit_reports: '各年度审计报告',
};
function _strategyLabel(strategy) {
    if (!strategy) return '未设置';
    if (strategy.startsWith('regex:')) return STRATEGY_LABELS['regex:'];
    return STRATEGY_LABELS[strategy] || strategy;
}
function _roleLabel(role) {
    if (!role) return '未设置';
    const fromRules = (_foundationRules?.source_roles || []).find(r => r.id === role);
    if (fromRules) return fromRules.label || role;
    const m = role.match(/^audit_report_(\d{4})$/);
    if (m) return `${m[1]}年度审计报告`;
    return ROLE_LABEL_FALLBACK[role] || role;
}

/** 数据中间层字段表：不按小节分组——同一个字段可能被多个小节复用，按小节分反而制造假边界。
 * 平铺 + 搜索，每行显示"用于哪些小节"（由后端扫生成模板算出，不是字段自己的固定归属）。 */
function _renderFoundationFieldsTable() {
    const fields = (_foundationData.fields || []).map((f, index) => ({ ...f, _index: index }));
    if (!fields.length) {
        return '<div class="foundation-empty"><h4>暂无数据中间层字段</h4><p>点击"重新提取数据"开始抽取。</p></div>';
    }
    const activeFields = fields.filter(f => f.status !== 'disabled');
    const deletedFields = fields.filter(f => f.status === 'disabled');
    const deletedGroups = [];
    const deletedKeys = new Set();
    deletedFields.forEach(f => {
        const match = String(f.id || '').match(/^finance\.([^.]+)\./);
        const key = match ? `finance.${match[1]}` : f.id;
        if (deletedKeys.has(key)) return;
        deletedKeys.add(key);
        deletedGroups.push({ ...f, _deletedLabel: match ? f.label.replace(/（.*$/, '') + '（全部期间）' : f.label });
    });
    return `<div class="foundation-section">
        <div class="foundation-section-head">
            <div><h4>数据中间层字段</h4><span>${activeFields.filter(f => f.value).length}/${activeFields.length} 已有值；两份业务人工输入表不在此列表，见"人工输入"页</span></div>
            <input class="form-input" id="foundationFieldSearch" placeholder="搜索字段名称或值…" oninput="_filterFoundationFieldsTable(this.value)" style="max-width:220px">
        </div>
        <div class="foundation-table-wrap"><table class="foundation-table foundation-fields-table" id="foundationFieldsTable">
            <thead><tr><th>字段名称</th><th>当前值</th><th>提取规则（从哪里 · 怎么取）</th><th>来源（点击溯源）</th><th>用于哪些小节</th><th>操作</th></tr></thead>
            <tbody>${activeFields.map(f => _renderFoundationField(f)).join('') || '<tr><td colspan="6" class="source-none">当前没有启用的字段</td></tr>'}</tbody>
        </table></div>
        ${deletedFields.length ? `<details class="foundation-deleted-fields">
            <summary>已删除字段（${deletedGroups.length} 组，共 ${deletedFields.length} 个期间字段）</summary>
            <div>${deletedGroups.map(f => `<span>${_escHtmlAttr(f._deletedLabel)}<button class="btn btn-ghost btn-sm" onclick="restoreFoundationField(${f._index})">恢复</button></span>`).join('')}</div>
        </details>` : ''}
    </div>`;
}

function _renderFileReextractionRuns(runs) {
    const rows = [...(runs || [])].reverse().slice(0, 8);
    if (!rows.length) return '';
    return `<details class="foundation-panel">
        <summary>按文件重提取记录（最近 ${rows.length} 次）</summary>
        <div class="foundation-validations">${rows.map(run => {
            const target = run.target_path ? run.target_path.split('/').pop() : _roleLabel(run.target_role || '');
            const status = run.status === 'completed' ? '已完成' : (run.status === 'failed' ? '失败' : '运行中');
            return `<div class="foundation-validation ${run.status === 'failed' ? 'failed' : ''}">
                <span>${run.status === 'completed' ? '✓' : '!'}</span>
                <div><b>${_escHtmlAttr(run.shared_rule_version || `运行批次 R${run.project_run_revision || 0}`)} · ${_escHtmlAttr(target)}</b>
                    <small>${_escHtmlAttr((run.completed_at || run.started_at || '').replace('T',' ').slice(0,19))}；关联 ${run.affected_field_ids?.length || 0} 个字段，值变化 ${run.changed_field_ids?.length || 0} 个${run.error ? `；${_escHtmlAttr(run.error)}` : ''}</small>
                    <details><summary>查看规则、候选文件和影响字段</summary><pre>${_escHtmlAttr(JSON.stringify({rule_snapshot:run.rule_snapshot, source_selection:run.source_selection, affected_field_ids:run.affected_field_ids, manual_override_field_ids:run.manual_override_field_ids}, null, 2))}</pre></details>
                </div><em>${status}</em>
            </div>`;
        }).join('')}</div>
    </details>`;
}

function _filterFoundationFieldsTable(keyword) {
    const kw = (keyword || '').trim().toLowerCase();
    document.querySelectorAll('#foundationFieldsTable tbody tr').forEach(row => {
        row.style.display = (!kw || (row.dataset.search || '').includes(kw)) ? '' : 'none';
    });
}

function _renderFoundationField(field) {
    // 值只读、来源只读——主要用来在编写时点击溯源；提取规则单独一列，用中文名不用内部英文
    // 标识；可以展开小表单改，改完可对该字段单独触发一次快速（文字层）重新生成。
    const [statusLabel, statusClass] = _foundationStatus(field.status);
    const source = field.source || {}, rule = field.rule || {};
    const sourceLine = source.path
        ? `<button class="foundation-source-link" onclick="openFoundationFieldSource(${field._index})">${_escHtmlAttr(source.path.split('/').pop())}</button><small>${_escHtmlAttr(source.locator || '')}</small>`
        : `<span class="source-none">${_escHtmlAttr(source.locator || field.extraction_note || '尚未执行或未命中来源')}</span>${(field.extraction_attempts || []).length ? `<details><summary>查看已检查范围</summary><pre>${_escHtmlAttr(JSON.stringify(field.extraction_attempts, null, 2))}</pre></details>` : ''}`;
    const decision = field.conflict_decision ? `<small class="conflict-reason">当前采用：${_escHtmlAttr(field.conflict_decision.selected || '')}。${_escHtmlAttr(field.conflict_decision.reason || '')}</small>` : '';
    const usedIn = (field.used_in_sections || []).map(sid => `<span class="foundation-badge">${_escHtmlAttr(sid)}</span>`).join('') || '<span class="source-none">暂未被任何小节引用</span>';
    const searchKey = _escHtmlAttr(`${field.label} ${field.value || ''} ${field.id}`.toLowerCase());
    const isRuntimeFinancial = String(field.extraction_plan?.template_rule_id || '').startsWith('financial_metrics.');
    const editableRole = isRuntimeFinancial ? 'audit_reports' : rule.source_role;
    const roleOptions = (_foundationRules?.source_roles || [])
        .map(r => `<option value="${_escHtmlAttr(r.id)}" ${editableRole === r.id ? 'selected' : ''}>${_escHtmlAttr(r.label || r.id)}</option>`).join('')
        + (editableRole && !(_foundationRules?.source_roles || []).some(r => r.id === editableRole)
            ? `<option value="${_escHtmlAttr(editableRole)}" selected>${_escHtmlAttr(_roleLabel(editableRole))}</option>` : '');
    const strategyOptions = Object.keys(STRATEGY_LABELS).filter(k => k !== 'regex:')
        .map(v => `<option value="${v}" ${(rule.strategy || field.strategy) === v ? 'selected' : ''}>${_escHtmlAttr(STRATEGY_LABELS[v])}</option>`).join('');
    const rawUnit = field.raw_unit ? `原表：${_escHtmlAttr(field.raw_value || '')} ${_escHtmlAttr(field.raw_unit)}` : '';
    const conversion = field.conversion?.formula ? `；换算：${_escHtmlAttr(field.conversion.formula)}` : '';
    const unitNote = rawUnit ? `<small>${rawUnit}${conversion}</small>` : '';
    const displayValue = field.value
        ? `${_escHtmlAttr(field.value).replace(/\n/g, '<br>')}${field.target_unit && !String(field.value).includes(field.target_unit) ? ` <em class="foundation-unit">${_escHtmlAttr(field.target_unit)}</em>` : ''}`
        : '<span class="source-none">（空）</span>';
    const plan = field.extraction_plan || {};
    const ruleCell = `<div><b>${_escHtmlAttr(_roleLabel(editableRole))}</b> · ${_escHtmlAttr(_strategyLabel(rule.strategy))}</div>
        ${rule.extract_prompt || rule.explanation ? `<small>${_escHtmlAttr(rule.extract_prompt || rule.explanation)}</small>` : ''}
        <small>本项目运行绑定：${_escHtmlAttr(plan.selected_path || source.path || '尚未命中')}${plan.period?.label ? ` · ${_escHtmlAttr(plan.period.label)}` : ''}</small>
        <details class="foundation-rule-edit">
            <summary>✎ 编辑通用字段抽取规则</summary>
            <div class="rule-edit-form">
                <label class="text-muted text-sm">材料类型（具体文件由项目目录自动匹配）</label>
                <select class="foundation-rule-input" id="fr-role-${field._index}" ${isRuntimeFinancial ? 'disabled' : ''}>${roleOptions}</select>
                <label class="text-muted text-sm">用什么方式提取</label>
                <select class="foundation-rule-input" id="fr-strategy-${field._index}">${strategyOptions}</select>
                <label class="text-muted text-sm">抽取 Prompt（写清报表/段落、口径、单位和找不到时怎么办）</label>
                <textarea class="foundation-rule-input" id="fr-prompt-${field._index}" rows="4">${_escHtmlAttr(rule.extract_prompt || rule.explanation || '')}</textarea>
                <button class="btn btn-primary btn-sm" id="fr-reextract-btn-${field._index}" onclick="saveAndRebuildFoundationField(${field._index})">保存通用规则并重跑本项目</button>
                <small class="text-muted">不保存具体文件名或年份；系统会重新从当前项目目录选文件，并更新同文件关联字段。人工覆盖值保留</small>
            </div>
        </details>`;
    return `<tr class="foundation-field-row ${field.required && !field.value ? 'required-missing' : ''}" data-search="${searchKey}">
        <td><b>${_escHtmlAttr(field.label)}</b>${field.required ? '<em class="required-mark">必填</em>' : ''}<span class="foundation-badge ${statusClass}">${statusLabel}</span></td>
        <td class="foundation-value-readonly">${displayValue}${unitNote}</td>
        <td>${ruleCell}</td>
        <td>${sourceLine}${decision}</td>
        <td>${usedIn}</td>
        <td><button class="btn btn-ghost btn-sm foundation-delete-btn" onclick="deleteFoundationField(${field._index})">删除</button></td>
    </tr>`;
}

function _foundationFieldFamily(field) {
    const match = String(field?.id || '').match(/^finance\.([^.]+)\./);
    if (!match) return [field];
    return (_foundationData.fields || []).filter(f => String(f.id || '').startsWith(`finance.${match[1]}.`));
}

async function _setFoundationFieldsDisabled(index, disabled) {
    const field = (_foundationData.fields || [])[index];
    if (!field) return;
    const family = _foundationFieldFamily(field);
    if (disabled) {
        const scope = family.length > 1 ? `“${field.label.replace(/（.*$/, '')}”全部期间` : `“${field.label}”`;
        if (!confirm(`确认删除${scope}？删除后不再抽取、统计或生成，可在页面底部恢复。`)) return;
    }
    try {
        await API.updateDataFoundationRules(family.map(f => ({ id: f.id, disabled })));
        const resp = await API.buildDataFoundation();
        _foundationData = resp.data;
        renderDataFoundation();
        showToast(disabled ? '字段已删除，不再参与抽取和章节生成' : '字段已恢复，将按原规则参与抽取和生成', 'success');
    } catch (e) {
        showToast(`${disabled ? '删除' : '恢复'}失败：${e.message}`, 'error');
    }
}

function deleteFoundationField(index) {
    return _setFoundationFieldsDisabled(index, true);
}

function restoreFoundationField(index) {
    return _setFoundationFieldsDisabled(index, false);
}

/** 保存模板级通用规则，并以当前项目命中的来源文件为边界重提取相关字段。 */
async function saveAndRebuildFoundationField(index) {
    const field = (_foundationData.fields || [])[index];
    if (!field) return;
    const role = document.getElementById(`fr-role-${index}`)?.value || '';
    const strategy = document.getElementById(`fr-strategy-${index}`)?.value || '';
    const extract_prompt = document.getElementById(`fr-prompt-${index}`)?.value || '';
    const isRuntimeFinancial = String(field.extraction_plan?.template_rule_id || '').startsWith('financial_metrics.');
    const update = {
            id: field.id, strategy, extract_prompt,
            source_label: field.rule?.source_label || '', required: !!field.required,
        };
    if (!isRuntimeFinancial) update.source_role = role;
    const target = field.extraction_plan?.selected_path?.split('/').pop() || _roleLabel(role);
    if (!confirm(`这是跨项目共用规则。保存“${field.label}”后，会在本项目重新匹配材料并提取“${target}”关联的字段。继续？`)) return;
    const button = document.getElementById(`fr-reextract-btn-${index}`);
    if (button) { button.disabled = true; button.textContent = '正在重新匹配并提取…'; }
    try {
        const resp = await API.updateRuleAndReextractFile(update);
        _foundationData = resp.data;
        renderDataFoundation();
        showToast(resp.message || '通用规则已保存，并完成本项目的文件重匹配与提取', 'success');
    } catch (e) {
        if (button) { button.disabled = false; button.textContent = '保存通用规则并重跑本项目'; }
        showToast('规则重提取失败：' + e.message, 'error');
    }
}

let _draftSourceRefs = [];  // 关键事实引用池：每项可含多个底稿/计算来源
let _draftSourceRefKeys = new Map();

function _draftCitationKey(citation) {
    const sources = (citation?.sources || []).map(s => [s.kind, s.path, s.page, s.locator, s.tool]);
    return JSON.stringify([citation?.field_id, citation?.display_value, sources]);
}

function _registerDraftCitation(citation) {
    const key = _draftCitationKey(citation);
    if (_draftSourceRefKeys.has(key)) return _draftSourceRefKeys.get(key);
    const idx = _draftSourceRefs.length;
    _draftSourceRefs.push(citation || {});
    _draftSourceRefKeys.set(key, idx);
    return idx;
}

function _citationBadge(citation) {
    if (!citation) return '';
    const idx = _registerDraftCitation(citation);
    const sources = citation.sources || [];
    const localSourceIndex = sources.findIndex(s => s.path);
    const sourceIndex = localSourceIndex >= 0 ? localSourceIndex : 0;
    const source = sources[sourceIndex] || {};
    const page = source.page ? `第${source.page}页` : '';
    const title = `${citation.label || citation.field_id || '关键事实'}${page ? ` · ${page}` : ''}：${source.locator || '查看来源'}`;
    return `<button class="fact-citation" title="${_escHtmlAttr(title)}" onclick="openDraftSourceRef(${idx},${sourceIndex});event.stopPropagation()">${idx + 1}</button>`;
}

function _renderProvenanceText(text, citations) {
    const value = String(text || '');
    const spans = [...(citations || [])].filter(c => Number.isInteger(c.start) && Number.isInteger(c.end) && c.end > c.start)
        .sort((a, b) => a.start - b.start || a.end - b.end);
    if (!spans.length) return _escHtmlAttr(value).replace(/\n/g, '<br>');
    let cursor = 0, html = '';
    spans.forEach(citation => {
        if (citation.start < cursor || citation.start > value.length) return;
        html += _escHtmlAttr(value.slice(cursor, citation.start)).replace(/\n/g, '<br>');
        const fact = value.slice(citation.start, Math.min(citation.end, value.length));
        html += `<span class="trace-fact">${_escHtmlAttr(fact).replace(/\n/g, '<br>')}${_citationBadge(citation)}</span>`;
        cursor = Math.min(citation.end, value.length);
    });
    return html + _escHtmlAttr(value.slice(cursor)).replace(/\n/g, '<br>');
}

function _renderCitationDetails(citations, label = '本段') {
    const unique = [];
    (citations || []).flat().forEach(citation => {
        if (!citation) return;
        const idx = _registerDraftCitation(citation);
        if (!unique.some(item => item.idx === idx)) unique.push({idx, citation});
    });
    if (!unique.length) return '';
    const rows = unique.map(({idx, citation}) => {
        const sources = citation.sources || [];
        const sourceRows = sources.length ? sources.map((source, sourceIndex) => {
            const name = source.path ? source.path.split('/').pop()
                : (source.kind === 'tianyancha' || source.kind === 'tyc' ? '天眼查查询'
                    : (source.kind === 'web_search' ? '公开网络检索' : (source.locator || '派生计算')));
            const page = source.page ? `第${source.page}页` : '';
            const locator = [page, source.locator].filter(Boolean).join(' · ');
            const action = source.path
                ? `<button class="foundation-source-link" onclick="openDraftSourceRef(${idx},${sourceIndex})">${_escHtmlAttr(name)}</button>`
                : `<span class="trace-source-name">${_escHtmlAttr(name)}</span>`;
            const quote = source.quote ? `<q>${_escHtmlAttr(String(source.quote).slice(0, 240))}</q>` : '';
            return `<div class="trace-source-row">${action}<small>${_escHtmlAttr(locator)}</small>${quote}</div>`;
        }).join('') : '<small class="source-none">当前字段尚无可点击底稿</small>';
        const formula = citation.conversion?.display_formula || citation.conversion?.formula || '';
        const conversion = formula
            ? `<small class="trace-conversion">处理：${_escHtmlAttr(formula)}${citation.raw_value ? `；原值 ${_escHtmlAttr(citation.raw_value)} ${_escHtmlAttr(citation.raw_unit || '')}` : ''}</small>` : '';
        return `<div class="trace-citation-row"><b><i>${idx + 1}</i>${_escHtmlAttr(citation.label || citation.field_id || '关键事实')}</b><em>${_escHtmlAttr(citation.display_value || '')}</em>${conversion}${sourceRows}</div>`;
    }).join('');
    return `<details class="section-source fact-source-list"><summary>${label}关键事实来源（${unique.length}）</summary>${rows}</details>`;
}

/** 把 src 文本（如 "〈1〉申报材料：a.pdf 〈摘录〉；〈2〉申报材料：b.pdf 〈摘录〉"）里的
 * 每个"申报材料：路径"解析成可点击定位原文的按钮；解析不出结构化引用时退回纯文本展示。 */
function _renderTraceableSrc(srcText) {
    if (!srcText) return '';
    const rows = String(srcText).split('；').filter(Boolean).map(part => {
        const m = part.match(/申报材料：(.*?)\s*〈([^〉]*)〉\s*$/);
        if (!m) return `<div><small>${_escHtmlAttr(part.trim())}</small></div>`;
        const pageMatch = String(m[2] || '').match(/第\s*(\d+)(?:[、,，\-—]\d+)*\s*页/);
        const r = {
            path: m[1].trim(),
            quote: String(m[2] || '').replace(/^第\s*\d+(?:[、,，\-—]\d+)*\s*页\s*[/｜|·-]*\s*/, ''),
            page: pageMatch ? Number(pageMatch[1]) : 0,
        };
        const idx = _draftSourceRefs.length;
        _draftSourceRefs.push(r);
        return `<div><button class="foundation-source-link" onclick="openDraftSourceRef(${idx})">${_escHtmlAttr(r.path.split('/').pop())}</button><small>${_escHtmlAttr(r.quote)}</small></div>`;
    }).join('');
    const clickable = rows.includes('foundation-source-link');
    return `<details class="section-source" open><summary>查看来源${clickable ? '（文件可点击定位原文）' : ''}</summary>${rows}</details>`;
}

function openDraftSourceRef(idx, sourceIndex = 0) {
    const citation = _draftSourceRefs[idx] || {};
    const sources = citation.sources || [];
    const source = sources[sourceIndex] || sources.find(s => s.path) || citation;
    if (source?.path) {
        openMaterialPreview(source.path, source.quote || source.matched_quote || '', source.page || 0);
        return;
    }
    showToast(source?.locator || `${citation.label || '该事实'}当前没有可打开的本地底稿`, source?.locator ? 'info' : 'error');
}

function _renderFoundationDraft(section) {
    if (!section) return '<div class="foundation-draft-doc">暂无草稿</div>';
    _draftSourceRefs = [];
    _draftSourceRefKeys = new Map();
    const blocks = (section.blocks || []).map(block => {
        const legacySrc = !(block.provenance || block.cell_provenance || (block.rows || []).some(r => r?.provenance?.length))
            ? _renderTraceableSrc(block.src) : '';
        if (block.type === 'p') {
            const citations = block.provenance || [];
            return `<div class="section-result-block"><p>${_renderProvenanceText(block.text || '', citations)}</p>${_renderCitationDetails(citations, '本段')}${legacySrc}</div>`;
        }
        if (block.type === 'kv') {
            const citations = (block.rows || []).flatMap(r => r.provenance || []);
            const table = (block.rows || []).map(r => `<tr><th>${_escHtmlAttr(r.label)}</th><td><span class="trace-cell-value">${_escHtmlAttr(r.value || '').replace(/\n/g, '<br>')}${(r.provenance || []).map(_citationBadge).join('')}</span></td></tr>`).join('');
            return `<div class="draft-table"><b>${_escHtmlAttr(block.caption || '')}</b><table>${table}</table>${_renderCitationDetails(citations, '本表')}${legacySrc}</div>`;
        }
        if (block.type === 'grid') {
            const matrix = block.cell_provenance || [];
            const citations = matrix.flat(2).filter(Boolean);
            const body = (block.rows || []).map((row, rowIndex) => `<tr>${row.map((cell, colIndex) => {
                const refs = matrix[rowIndex]?.[colIndex] || [];
                return `<td><span class="trace-cell-value">${_escHtmlAttr(cell || '')}${refs.map(_citationBadge).join('')}</span></td>`;
            }).join('')}</tr>`).join('');
            return `<div class="draft-table"><b>${_escHtmlAttr(block.caption || '')}</b><table><thead><tr>${(block.headers || []).map(h => `<th>${_escHtmlAttr(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>${_renderCitationDetails(citations, '本表')}${legacySrc}</div>`;
        }
        return '';
    }).join('');
    return `<div class="foundation-draft-doc"><h4>${_escHtmlAttr(section.title || '')}</h4>${blocks}</div>`;
}

function approveFoundationSection(sectionId) {
    (_foundationData.fields || []).forEach(f => {
        if (f.section_id === sectionId && String(f.value || '').trim()) {
            f.review ||= {};
            f.review.status = 'approved';
        }
    });
    renderDataFoundation();
}

function openFoundationFieldSource(index) {
    const field = (_foundationData.fields || [])[index] || {};
    const source = field.source || {};
    if (source.path) openMaterialPreview(source.path, source.quote || source.matched_quote || '', source.page || 0);
}

async function saveDataFoundation(silent) {
    if (!_foundationData) return false;
    const updates = (_foundationData.fields || []).map(f => ({
        id: f.id,
        value: f.value || '',
    }));
    try {
        const resp = await API.updateDataFoundation(updates);
        _foundationData = resp.data;
        renderDataFoundation();
        if (!silent) showToast('数据中间层修订已保存；修订值将覆盖本次 AI 抽取结果');
        return true;
    } catch (e) {
        showToast('保存失败：' + e.message, 'error');
        return false;
    }
}

async function applyDataFoundationDrafts() {
    if (!_foundationData) return;
    if (!await saveDataFoundation(true)) return;
    try {
        const resp = await API.applyDataFoundationDrafts();
        _previewCache = {};
        _markStepperDone(1); _markStepperDone(2);
        showToast(resp.message || '两节草稿已写入对应章节，可打开第一章/第二章查看和下载');
    } catch (e) {
        showToast('写入草稿失败：' + e.message, 'error');
    }
}

/** 尚无小节级 Know-how 的官方小节：不提供生成入口，引导业务去 Know-how 页补充原文。 */
function selectUnconfiguredSection(sectionId, title) {
    currentChapter = `section-${sectionId}`;
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    container.innerHTML = `
        <div class="foundation-empty" style="padding:32px;text-align:center">
            <h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">${_escHtmlAttr(sectionId)} ${_escHtmlAttr(title)}</h3>
            <p class="text-muted text-sm" style="margin-top:8px">这一节还没有业务 Know-how，暂时无法生成。</p>
            <button class="btn btn-primary" style="margin-top:12px" onclick="goToKnowhow('${_escHtmlAttr(sectionId)}')">去 Know-how 页补充</button>
        </div>`;
}

let _skillSectionView = 'result';  // result（生成结果+溯源） | word（Word 排版预览，无溯源标记，同最终导出）
let _skillSectionData = null;      // 当前小节最近一次加载的 {config, section, generated}

async function selectSkillSection(sectionId) {
    currentChapter = `section-${sectionId}`;
    _skillSectionView = 'result';
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    container.innerHTML = '<div class="text-muted" style="padding:24px">正在加载小节生成结果…</div>';
    try {
        const resp = await API.getSkillSection(sectionId);
        _skillSectionData = resp.data || {};
        _renderSkillSectionDetail(sectionId);
    } catch (e) { container.innerHTML = `<div class="foundation-error">小节加载失败：${_escHtmlAttr(e.message)}</div>`; }
}

function _renderSkillSectionDetail(sectionId) {
    const container = document.getElementById('chapterDetail');
    const d = _skillSectionData || {}, cfg = d.config || {};
    if (!container) return;
    container.innerHTML = `
        <div class="chapter-detail-header"><div><h3>${_escHtmlAttr(sectionId)} ${_escHtmlAttr(cfg.title || '')}</h3><div class="text-muted text-sm">${_escHtmlAttr(cfg.chapter_title || '')}</div></div><div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="goToKnowhow('${sectionId}')">查看 Know-how 原文</button><button class="btn btn-ghost btn-sm" onclick="downloadChapterWord(${cfg.chapter_n || 0})" title="下载第${cfg.chapter_n || ''}章完整 Word（含本章全部小节，不止这一节）">下载本章 Word</button><button class="btn btn-ghost btn-sm" onclick="auditSkillSection('${sectionId}',${cfg.chapter_n || 0},'${_escHtmlAttr(cfg.title || '')}')">审核本小节</button><button class="btn btn-primary" onclick="generateSkillSectionUI('${sectionId}')">${d.generated ? '重新生成本小节' : '生成本小节'}</button></div></div>
        <div class="chapter-detail-body">
            <div class="section-generation-note">生成只读取当前数据中间层和该小节 Know-how，不会调用旧的大章 Agent，也不会改动同章其他小节。</div>
            ${d.generated ? `
                <div class="sk-seg" style="margin-bottom:12px">
                    <div class="sk-seg-item ${_skillSectionView === 'result' ? 'active' : ''}" onclick="_switchSkillSectionView('result','${sectionId}')">生成结果 · 可溯源</div>
                    <div class="sk-seg-item ${_skillSectionView === 'word' ? 'active' : ''}" onclick="_switchSkillSectionView('word','${sectionId}')">Word 排版预览（本节）</div>
                </div>
                <div id="skillSectionViewBody">${_skillSectionView === 'result' ? _renderFoundationDraft(d.section) : '<div class="text-muted text-sm" style="padding:8px 0">加载中…</div>'}</div>
            ` : '<div class="foundation-empty"><h4>本小节尚未生成</h4><p>请先完成数据提取并检查规则，然后点击“生成本小节”。</p></div>'}
        </div>`;
    if (d.generated && _skillSectionView === 'word') {
        _loadSkillSectionWordPreview(sectionId);
    }
}

function _switchSkillSectionView(view, sectionId) {
    _skillSectionView = view;
    _renderSkillSectionDetail(sectionId);
}

/** 只渲染这一节，不混进同章其它小节（哪怕还没配置/没生成）；整章合并下载走标题栏
 * "下载本章 Word" 按钮，两件事分开。 */
async function _loadSkillSectionWordPreview(sectionId) {
    const body = document.getElementById('skillSectionViewBody');
    if (!body || !sectionId) return;
    try {
        const resp = await API.getSectionPreview(sectionId);
        body.innerHTML = resp.has_content
            ? `${_gateWarnHtml(resp.gate_warnings)}<div class="word-preview-frame">${resp.html}</div>`
            : '<div class="text-muted text-sm" style="padding:8px 0">暂无内容</div>';
    } catch (e) {
        body.innerHTML = `<div class="foundation-error">预览加载失败：${_escHtmlAttr(e.message)}</div>`;
    }
}

async function generateSkillSectionUI(sectionId) {
    try {
        const resp = await API.generateSkillSection(sectionId);
        showToast(resp.message || '小节生成完成');
        _previewCache = {};
        await renderChapterStepper();
        await selectSkillSection(sectionId);
    } catch (e) { showToast('小节生成失败：' + e.message, 'error'); }
}

/** 一次生成某章全部已配置二级小节；这是小节 Skill 的批处理，不回退调用旧大章 Agent。 */
async function generateSkillChapterUI(chapterN, generatedCount = 0) {
    if (generatedCount > 0 && !confirm(`第${chapterN}章已有 ${generatedCount} 个小节生成过，继续会按当前数据和 Skill 覆盖这些小节。是否继续？`)) return;
    const btn = document.getElementById(`chapterGenerateBtn${chapterN}`);
    const oldText = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '本章生成中…'; }
    try {
        const resp = await API.generateSkillChapter(chapterN);
        _previewCache = {};
        await renderChapterStepper();
        _renderChapterBatchResult(resp.data || {});
        showToast(resp.message || `第${chapterN}章批量生成完成`, (resp.data || {}).failed_total ? 'warning' : 'success');
    } catch (e) {
        showToast(`第${chapterN}章生成失败：` + e.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = oldText; }
    }
}

function _renderChapterBatchResult(data) {
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    const n = data.chapter_n || 0;
    currentChapter = `section-chapter-${n}`;
    const rows = (items, kind) => (items || []).map(item => `
        <div class="chapter-batch-row ${kind}">
            <span>${_escHtmlAttr(item.id || '')} ${_escHtmlAttr(item.title || '')}</span>
            <span>${kind === 'success' ? '已生成' : (kind === 'failed' ? _escHtmlAttr(item.error || '生成失败') : _escHtmlAttr(item.reason || '已跳过'))}</span>
        </div>`).join('');
    container.innerHTML = `
        <div class="chapter-detail-header">
            <div><h3>第${n}章 ${_escHtmlAttr(data.chapter_title || '')}</h3><div class="text-muted text-sm">本次按小节 Skill 批量生成结果</div></div>
            <div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="downloadChapterWord(${n})">下载本章 Word</button><button class="btn btn-primary btn-sm" onclick="generateSkillChapterUI(${n},${data.generated_total || 0})">重新生成本章</button></div>
        </div>
        <div class="chapter-detail-body">
            <div class="section-generation-note">已配置小节逐节生成；未配置 Know-how 的小节不会调用旧的大章 Agent，待业务补齐规则后会自动纳入下一次整章生成。</div>
            <div class="chapter-batch-summary">
                <span>成功 ${data.generated_total || 0}</span><span>失败 ${data.failed_total || 0}</span><span>跳过 ${data.skipped_total || 0}</span>
            </div>
            ${rows(data.generated_sections, 'success')}
            ${rows(data.failed_sections, 'failed')}
            ${rows(data.skipped_sections, 'skipped')}
        </div>`;
}

async function auditSkillSection(sectionId, chapterN, title) {
    try {
        await API.runReportAudit({scope:'chapter', chapter_n:chapterN, section_title:title, use_ai:true});
        showToast('小节审核完成，可在“报告审核”查看问题');
    } catch (e) { showToast('小节审核失败：' + e.message, 'error'); }
}

async function selectReportAudit() {
    currentChapter = 'report-audit';
    const container = document.getElementById('chapterDetail');
    if (!container) return;
    container.innerHTML = `
        <div class="chapter-detail-header"><div><h3 style="font-size:14px;font-weight:600;color:var(--text-primary)">报告审核</h3><div class="text-muted text-sm" style="margin-top:4px">审核对象是生成后的报告，不是数据中间层；意见仅提示，永远不阻止 Word 导出</div></div><div class="flex gap-8"><button class="btn btn-ghost btn-sm" onclick="runFullReportAudit(false)">仅规则检查</button><button class="btn btn-ghost btn-sm" onclick="runFullReportAudit(true)">运行完整 AI 审核</button><button class="btn btn-primary btn-sm" onclick="runWholeReportAudit()" title="全部小节都写完之后运行一次，只找跨小节才能发现的矛盾；校验要点在 Skill 管理页的“全文一致性校验 Know-how”里维护">运行全文一致性校验</button></div></div>
        <div class="chapter-detail-body" id="reportAuditBody"><div class="text-muted" style="padding:16px">正在加载审核结果…</div></div>`;
    await loadReportAudit();
}

async function loadReportAudit() {
    const body = document.getElementById('reportAuditBody');
    if (!body) return;
    try {
        const resp = await API.getReportAudit();
        renderReportAudit(resp.data || {});
    } catch (e) { body.innerHTML = `<div class="foundation-error">审核结果加载失败：${_escHtmlAttr(e.message)}</div>`; }
}

function _renderWholeReportAudit(wr) {
    if (!wr) return '';
    const issues = wr.issues || [];
    return `<details class="audit-run" open style="border-color:var(--primary)">
        <summary><span>全文一致性校验（跨小节，共检查 ${wr.sections_checked || 0} 节）</span><em>${wr.stats?.total || 0} 项 · ${_escHtmlAttr((wr.audited_at || '').replace('T',' ').slice(0,19))}</em></summary>
        ${wr.stale ? `<div class="foundation-alert warn">${_escHtmlAttr(wr.stale_reason || '章节内容已变化，本结果已过期，请重新运行全文一致性校验。')}</div>` : ''}
        ${wr.error ? `<div class="foundation-alert warn">${_escHtmlAttr(wr.error)}</div>` : ''}
        <div class="audit-issues">${issues.length ? issues.map(issue => `<div class="audit-issue ${issue.severity || 'warning'}"><span>${issue.severity === 'error' ? '错误' : (issue.severity === 'info' ? '提示' : '警告')}</span><div><b>${_escHtmlAttr(issue.description || '')}</b><small>${_escHtmlAttr(issue.location || '')}</small>${issue.suggestion ? `<p>建议：${_escHtmlAttr(issue.suggestion)}</p>` : ''}</div></div>`).join('') : (wr.error ? '' : '<div class="audit-clean">✓ 未发现跨小节矛盾</div>')}</div>
    </details>`;
}

function renderReportAudit(data) {
    const body = document.getElementById('reportAuditBody');
    if (!body) return;
    const runs = Object.values(data.runs || {}).sort((a,b) => (a.key || '').localeCompare(b.key || ''));
    const issues = runs.flatMap(r => r.issues || []);
    body.innerHTML = `
        <div class="foundation-flow"><span>已审核小节 <b>${runs.length}</b></span><i>→</i><span>错误 <b>${issues.filter(i => i.severity === 'error').length}</b></span><i>→</i><span>警告 <b>${issues.filter(i => i.severity === 'warning').length}</b></span><i>→</i><span>提示 <b>${issues.filter(i => i.severity === 'info').length}</b></span></div>
        ${_renderWholeReportAudit(data.whole_report)}
        ${!runs.length ? '<div class="foundation-empty"><h4>尚无小节审核结果</h4><p>生成小节后会自动审核该节；也可以点击“运行完整 AI 审核”复核当前全部报告。</p></div>' : runs.map(run => `<details class="audit-run" open><summary><span>${_escHtmlAttr(run.title || run.key)}</span><em>${run.stats?.total || 0} 项 · AI ${run.ai_status === 'completed' ? '已完成' : (run.ai_status === 'failed' ? '失败，已保留规则检查' : '未配置或无新增问题')}</em></summary>${run.ai_error ? `<div class="foundation-alert warn">AI 审核失败：${_escHtmlAttr(run.ai_error)}。不影响导出。</div>` : ''}${String(run.section_id || '').includes('.') && ((run.issues || []).length || run.ai_status === 'failed') ? `<div style="padding:8px 12px"><button class="btn btn-primary btn-sm" onclick="regenerateAuditSection('${_escHtmlAttr(run.section_id)}')">重新生成并审核该小节</button></div>` : ''}<div class="audit-issues">${(run.issues || []).length ? (run.issues || []).map(issue => `<div class="audit-issue ${issue.severity || 'warning'}"><span>${issue.severity === 'error' ? '错误' : (issue.severity === 'info' ? '提示' : '警告')}</span><div><b>${_escHtmlAttr(issue.description || '')}</b><small>${_escHtmlAttr(issue.location || '')}</small>${issue.suggestion ? `<p>建议：${_escHtmlAttr(issue.suggestion)}</p>` : ''}${issue.evidence ? `<details><summary>查看依据</summary><pre>${_escHtmlAttr(issue.evidence)}</pre></details>` : ''}</div></div>`).join('') : '<div class="audit-clean">✓ 本轮未发现问题</div>'}</div></details>`).join('')}`;
}

async function regenerateAuditSection(sectionId) {
    if (!sectionId || !confirm(`将按当前数据中间层和生成 SKILL 覆盖小节 ${sectionId}，随后重新运行该节 AI 审核。确定继续？`)) return;
    try {
        const generated = await API.generateSkillSection(sectionId);
        const cfg = generated?.data?.config || {};
        await API.runReportAudit({
            scope: 'chapter', chapter_n: cfg.chapter_n || Number(sectionId.split('.')[0]),
            section_title: cfg.title || '', use_ai: true,
        });
        showToast(`${sectionId} 已重新生成并完成复核`, 'success');
        await loadReportAudit();
    } catch (e) { showToast('重新生成或审核失败：' + e.message, 'error'); }
}

async function runWholeReportAudit() {
    const body = document.getElementById('reportAuditBody');
    if (body) body.insertAdjacentHTML('afterbegin', `<div class="foundation-alert warn" id="auditRunning">正在运行全文一致性校验（跨小节比对，可能需要一点时间）…</div>`);
    try {
        const resp = await API.runWholeReportAudit();
        renderReportAudit(resp.data || {});
        showToast('全文一致性校验完成；结果仅供业务复核，不阻止 Word 导出');
    } catch (e) {
        document.getElementById('auditRunning')?.remove();
        showToast('全文一致性校验失败：' + e.message, 'error');
    }
}

async function runFullReportAudit(useAi) {
    const body = document.getElementById('reportAuditBody');
    if (body) body.insertAdjacentHTML('afterbegin', `<div class="foundation-alert warn" id="auditRunning">正在${useAi ? '逐小节运行 AI 审核' : '执行规则检查'}；审核意见不会阻止导出，请稍候…</div>`);
    try {
        const resp = await API.runReportAudit({scope:'report', use_ai:!!useAi});
        renderReportAudit(resp.data || {});
        showToast('报告审核完成；结果仅供业务复核，不阻止 Word 导出');
    } catch (e) {
        document.getElementById('auditRunning')?.remove();
        showToast('报告审核失败：' + e.message, 'error');
    }
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
                <button class="btn btn-ghost btn-sm" onclick="document.getElementById('summaryExcelInput').click()">${ICN.download} 上传Excel导入</button>
                <input type="file" id="summaryExcelInput" accept=".xlsx,.xls" style="display:none" onchange="importSummaryExcel(this)">
                <button class="btn btn-primary btn-sm" onclick="saveSummary()">${ICN.save} 保存</button>
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
let _domChapter = null;      // 编辑区 DOM 实际展示的章节号：保存必须跟随它（切章渲染空窗期 _editorChapter 已指向新章而 DOM 还是旧章，跟错会把旧章内容串写进新章）
let _renderSeq = 0;          // renderChapterEditor 序号：多次异步渲染交叠时只允许最新一次落 DOM，防 DOM 与章节号错配
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
    if (btn) btn.innerHTML = ICN.file + (_previewOn ? ' 关闭Word预览' : ' 开启Word预览');
    if (_previewOn) refreshChapterPreview();
}

/**
 * 渲染第 n 章编辑视图：各子标题下是 Word 式整块可编辑区（内容来自 Kimi 的可读文本）
 * 点击步骤条对应章节时调用
 */
async function renderChapterEditor(n) {
    _editorChapter = n;
    const seq = ++_renderSeq;
    currentChapter = 'chapter' + n;
    const container = document.getElementById('chapterDetail');
    if (!container) return;

    // 小标题骨架由后端自动回退到材料包内置官方模板；即使还没生成也能看到本章小标题
    let content = { source: 'none', sections: [] };
    try {
        content = await API.getChapterContent(n);
    } catch (e) { /* 后端未就绪，按空处理 */ }
    if (seq !== _renderSeq) return;   // 更新的章节渲染已接管：丢弃过期结果，避免旧 DOM 与新章节号错配导致串章保存
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
                <button class="btn btn-ghost btn-sm" id="btnChapterGen" onclick="runKimiChapter()">${ICN.robot} ${content.source === 'ready' ? '重新生成' : 'AI 生成'}</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertFootnote()" title="把光标放到正文中要加脚注的位置，再点此">${ICN.plus} 脚注</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="insertDiagram()" title="把光标放到正文中要插图的位置，再点此画框图">${ICN.image} 画图</button>
                <button class="btn btn-ghost btn-sm" onmousedown="event.preventDefault()" onclick="openKimiChat()" title="打开 Kimi 助手：多轮对话，可粘贴文字/贴链接/上传文件，回复可一键插入正文">${ICN.chat} Kimi 助手</button>
                <button class="btn btn-ghost btn-sm" id="btnChapterPreviewToggle" onclick="toggleChapterPreview()">${ICN.file} ${_previewOn ? '关闭Word预览' : '开启Word预览'}</button>
                <button class="btn btn-ghost btn-sm" onclick="downloadChapterWord(_editorChapter)" title="自动保存后导出本章最新 Word（项目名_日期_第n章_vN）">${ICN.download} 下载Word</button>
                    <button class="btn btn-ghost btn-sm" onclick="generateChapterDoc(_editorChapter)" title="把当前保存内容生成为新版本 Word，不触发下载">${ICN.file} 生成该文档</button>
                <button class="btn btn-primary btn-sm" onclick="saveChapter()">${ICN.save} 保存</button>
                    <span id="autosaveHint" class="text-muted" style="font-size:12px;margin-left:8px">${_autosaveHintText}</span>
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

    // 表格数据勾稽提醒（黄色提示，只提醒不阻断保存；无问题时不展示）
    if (content.table_check && content.table_check.length) {
        const tips = content.table_check.map(t => `<div>⚠ ${_escHtmlAttr(t.message)}</div>`).join('');
        html += `<div class="table-check-warn" style="margin:6px 0;padding:8px 10px;border-radius:6px;background:#fff7e6;border:1px solid #ffd591;color:#874d00;font-size:13px;line-height:1.7;">
            <div style="font-weight:600;">以下表格数据建议人工复核：</div>${tips}
        </div>`;
    }

    // 上次生成不完整的提醒（重新生成成功后自动消失）
    if (content.generation_notice) {
        html += `<div style="margin:6px 0;padding:8px 10px;border-radius:6px;background:#fff7e6;border:1px solid #ffd591;color:#874d00;font-size:13px;line-height:1.7;">⚠ ${_escHtmlAttr(content.generation_notice)}</div>`;
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
                <span>${ICN.file} Word 实时预览</span>
                <span class="flex gap-8">
                    <button class="btn btn-ghost btn-sm" onclick="downloadChapterWord(${n})">${ICN.download} 下载Word</button>
                </span>
            </div>
            <div id="ch1PreviewBody" class="ch1-preview-body">
                <div class="text-muted text-sm" style="padding:8px 0;">保存后可在此预览 Word 输出效果。</div>
            </div>
        </div>
    </div>`;

    container.innerHTML = html;
    _domChapter = n;   // DOM 此刻起展示的是第 n 章：自动暂存/关页补存一律按它落盘

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

/** 门禁提示条（业务语言黄色提醒，与预览 HTML 一起缓存） */
function _gateWarnHtml(warnings) {
    if (!warnings || !warnings.length) return '';
    const tips = warnings.map(w => `<div>⚠ ${_escHtmlAttr(w)}</div>`).join('');
    return `<div style="margin:0 0 8px;padding:8px 10px;border-radius:6px;background:#fff7e6;border:1px solid #ffd591;color:#874d00;font-size:13px;line-height:1.7;">${tips}</div>`;
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
            const full = _gateWarnHtml(resp.gate_warnings) + resp.html;
            body.innerHTML = full;
            _previewCache[n] = full;  // 缓存（含门禁提示），供下次开关预览直接用
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
                if (btn2) { btn2.disabled = false; btn2.innerHTML = ICN.robot + ' AI 生成'; }
                if (banner2) { banner2.className = 'kimi-status error'; banner2.textContent = '生成失败：' + (st.error || '未知错误'); }
            }
            showToast(`生成失败：${_chapterTitle(n) || '本章'}`, 'error');
        }
    }, 3000);
}

/**
 * 保存当前章：收集每个子标题的 Word 编辑区内容，回传给 reading skill（持久化）
 */
/** 静默落盘编辑区内容（不弹提示、不刷预览）：手动保存/导出前自动保存/定期暂存 共用 */
async function _persistChapter() {
    const n = _domChapter;   // 按 DOM 实际所属章节保存（切章空窗期 _editorChapter 已切走、DOM 还是旧章，跟它会串章）
    if (!n) return false;
    const editors = document.querySelectorAll('#chapterDetail .doc-editor');
    if (!editors.length) return false;
    const sections = Array.from(editors).map(ed => ({
        id: ed.dataset.secid || '',
        title: ed.dataset.title || '',
        html: ed.innerHTML,
    }));
    await API.saveChapterContent(n, sections);
    delete _previewCache[n];   // 内容已改，缓存作废
    return true;
}

async function saveChapter(silent) {
    try {
        const ok = await _persistChapter();
        if (!ok) { if (!silent) showToast('没有可保存的内容，请先生成', 'warning'); return false; }
        if (!silent) showToast('已保存，并返回给 reading skill');
        // 预览开启时，保存后自动让 writing skill 写入 Word 并刷新预览（强制重生成）
        if (_previewOn) refreshChapterPreview(true);
        return true;
    } catch (e) {
        if (silent) throw e;
        showToast('保存失败: ' + e.message, 'error');
        return false;
    }
}

// ===== 编辑区定期暂存（防意外退出丢失未保存内容）=====
let _editorDirty = false;     // 编辑区是否有未保存改动
let _autoSaving = false;      // 防重入锁
let _autosaveHintText = '';   // 工具栏暂存提示（模板重渲染时保留）

/** 编辑区任何内容变化都记为“有未保存改动”（打字/粘贴/插图等程序性修改都算）；
 * 每 30 秒静默暂存一次；关页/切后台时再补存一次（keepalive 保证送达）。 */
function _initAutosave() {
    const root = document.getElementById('chapterDetail');
    if (root && typeof MutationObserver !== 'undefined') {
        new MutationObserver(() => { _editorDirty = true; })
            .observe(root, { subtree: true, childList: true, characterData: true });
    }
    setInterval(async () => {
        if (!_editorDirty || _autoSaving) return;
        if (!AuthToken.get()) return;   // 登录态失效时不重试，避免 401 刷屏（重新登录后自动恢复）
        const pg = document.getElementById('page-ndrc');
        if (!pg || !pg.classList.contains('active')) return;
        _autoSaving = true;
        try {
            if (await _persistChapter()) {
                _editorDirty = false;
                const t = new Date();
                _autosaveHintText = `已自动暂存 ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
                const hint = document.getElementById('autosaveHint');
                if (hint) hint.textContent = _autosaveHintText;
            }
        } catch (e) {
            console.warn('[REIT-AI] 自动暂存失败，稍后重试:', e.message);
        } finally { _autoSaving = false; }
    }, 30000);
    const lastChance = () => {
        if (!_editorDirty) return;
        if (!AuthToken.get()) return;   // 登录态失效时不补存
        const n = _domChapter;   // 同 _persistChapter：按 DOM 所属章节补存，防切章空窗串章
        if (!n) return;
        const editors = document.querySelectorAll('#chapterDetail .doc-editor');
        if (!editors.length) return;
        const sections = Array.from(editors).map(ed => ({
            id: ed.dataset.secid || '', title: ed.dataset.title || '', html: ed.innerHTML,
        }));
        fetch(`/api/skills/chapter/${n}/save?project_id=${encodeURIComponent(currentProjectId || '')}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...AuthToken.headers() },
            body: JSON.stringify({ sections }),
            keepalive: true,
        }).catch(() => {});
        _editorDirty = false;
    };
    window.addEventListener('pagehide', lastChance);
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') lastChance();
    });
}

/** 工具栏/预览面板“⬇ 下载Word”：先静默自动保存编辑区，再导出新版本下载，保证所见即所得 */
async function downloadChapterWord(n) {
    try {
        const ok = await saveChapter(true);
        if (!ok) return;
    } catch (e) {
        showToast('保存失败，已取消下载: ' + e.message, 'error');
        return;
    }
    await API.downloadChapterDocx(n);
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

/* ============ 💬 Kimi 助手（右侧抽屉，多轮对话） ============ */
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
      .kimi-sel{padding:6px 14px;font-size:12px;color:#2e7d32;background:#eaf6ec;border-bottom:1px solid #eef1f4;
        display:flex;align-items:center;justify-content:space-between;gap:8px;}
      .kimi-sel button{border:none;background:transparent;cursor:pointer;color:#888;}
      .kimi-msgs{flex:1;overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;}
      .kimi-empty{color:#999;font-size:13px;text-align:center;margin-top:28px;line-height:1.7;}
      .kimi-msg{max-width:88%;padding:8px 11px;border-radius:10px;line-height:1.55;white-space:pre-wrap;word-break:break-word;}
      .kimi-msg.user{align-self:flex-end;background:#4CAF50;color:#fff;}
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
      .kimi-input-grip:hover::before{background:#4CAF50;}
      .kimi-input-row{display:flex;align-items:flex-end;gap:8px;padding:10px 14px;}
      .kimi-input-row textarea{flex:1;resize:none;min-height:40px;max-height:none;padding:8px;border:1px solid #dfe4ea;border-radius:8px;font-size:14px;box-sizing:border-box;}
      body.kimi-vresizing{user-select:none;cursor:ns-resize;}
      .kimi-iconbtn{border:1px solid #dfe4ea;background:#fff;border-radius:8px;padding:8px 10px;cursor:pointer;
        color:#555;line-height:0;display:flex;align-items:center;justify-content:center;}
      .kimi-iconbtn:hover{background:#eaf6ec;color:#388E3C;border-color:#cde5cf;}
      /* 左边整条都可抓着拖动改宽度；左上角露出一个圆弧手柄作为提示 */
      .kimi-resize{position:absolute;left:0;top:0;bottom:0;width:6px;cursor:col-resize;z-index:2;}
      .kimi-resize:hover{background:rgba(76,175,80,.12);}
      .kimi-grip{position:absolute;left:-9px;top:16px;width:18px;height:40px;border-radius:12px 0 0 12px;
        background:#4CAF50;cursor:col-resize;z-index:3;display:flex;align-items:center;justify-content:center;
        box-shadow:-2px 0 6px rgba(20,30,45,.18);}
      .kimi-grip::before{content:'';width:6px;height:16px;
        border-left:2px solid rgba(255,255,255,.85);border-right:2px solid rgba(255,255,255,.85);}
      body.kimi-resizing{user-select:none;cursor:col-resize;}
      body.kimi-open{transition:padding-right .2s;}
      body.kimi-resizing{transition:none;}
      /* Kimi 助手打开时，依据预览抽屉整体左移让位，避免两者叠盖、中间留空白带 */
      body.kimi-open .mat-preview-overlay{right:var(--kimi-w,0px);transition:right .2s;}
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
        <span>${ICN.chat} Kimi 助手</span>
        <span class="khbtns">
          <button id="kimiNewChat" title="清空，开一个新对话">${ICN.trash} 新对话</button>
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
    // 消息区的“复制/重新生成”按钮（事件委托）
    d.querySelector('#kimiMsgs').addEventListener('click', (e) => {
        const b = e.target.closest('button[data-act]');
        if (!b) return;
        const i = parseInt(b.dataset.i, 10);
        const msg = _kimiHistory[i];
        if (!msg) return;
        if (b.dataset.act === 'copy') _copyText(msg.content);
        else if (b.dataset.act === 'regen') _kimiRegen(i);
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
        document.body.style.setProperty('--kimi-w', w + 'px');
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
    document.body.style.setProperty('--kimi-w', w + 'px');
    if (_kimiInputH) document.getElementById('kimiInput').style.height = _kimiInputH + 'px';
    document.getElementById('kimiInput').focus();
}

function closeKimiChat() {
    if (_kimiDrawer) _kimiDrawer.style.display = 'none';
    document.body.classList.remove('kimi-open');
    document.body.style.paddingRight = '';
    document.body.style.removeProperty('--kimi-w');
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
            `<button data-act="copy" data-i="${i}">复制</button>` +
            `<button data-act="regen" data-i="${i}">重新生成</button></div>`;
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

/** 复制文字：线上是 HTTP（非安全上下文）没有 navigator.clipboard，回退 execCommand 保证能复制 */
function _copyText(t) {
    t = String(t == null ? '' : t);
    if (!t) { showToast('没有可复制的内容', 'warning'); return; }
    const ok = () => showToast('已复制');
    const fallback = () => {
        const ta = document.createElement('textarea');
        ta.value = t;
        ta.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0;';
        document.body.appendChild(ta);
        ta.focus(); ta.select();
        let done = false;
        try { done = document.execCommand('copy'); } catch (e) { done = false; }
        ta.remove();
        if (done) ok(); else showToast('复制失败，请手动选择文字复制', 'warning');
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).then(ok).catch(fallback);
    } else fallback();
}

/** 重新生成：把该条回复对应的用户提问再发一遍，新回复覆盖旧回复 */
async function _kimiRegen(i) {
    let j = i - 1;
    while (j >= 0 && _kimiHistory[j].role !== 'user') j--;
    if (j < 0) { showToast('找不到对应的提问，无法重新生成', 'warning'); return; }
    const old = _kimiHistory[i].content;
    _kimiHistory[i] = { role: 'assistant', content: '⏳ 重新生成中…' };
    _kimiRenderMsgs();
    const sendBtn = document.getElementById('kimiSend');
    sendBtn.disabled = true;
    try {
        const fd = new FormData();
        fd.append('history', JSON.stringify(_kimiHistory.slice(0, j)));
        fd.append('message', _kimiHistory[j].content.replace(/\n?📎 已附素材$/, ''));
        const r = await API.aiChat(fd);
        _kimiHistory[i] = { role: 'assistant', content: r.reply || '(空回复)' };
    } catch (e) {
        _kimiHistory[i] = { role: 'assistant', content: old };
        showToast('重新生成失败：' + (e.message || e), 'error');
    } finally {
        sendBtn.disabled = false;
        _kimiRenderMsgs();
    }
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
 * 底部整体进度：仅统计当前注册的小节 Skill，不再读取七个大章的旧状态。
 */
async function updateChapterProgress() {
    const fill = document.getElementById('progressFill');
    const label = document.getElementById('progressPercent');
    if (!fill || !label) return;

    if (!currentProjectId) {
        fill.style.width = '0%';
        label.textContent = '0%';
        return;
    }
    let sections = [];
    try { sections = (await API.listSkillSections()).sections || []; } catch (_) {}
    const done = sections.filter(section => section.generated).length;
    const percent = sections.length ? Math.round(done / sections.length * 100) : 0;
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
                <td><strong>${_escHtmlAttr(doc.title)}</strong> <span class="badge badge-primary">v${doc.version}</span><br><span class="text-muted text-sm">${_escHtmlAttr(doc.filename)}</span></td>
                <td>${_escHtmlAttr(_fmtTime(doc.updated_at))}</td>
                <td>${_escHtmlAttr(doc.size_formatted)}</td>
                <td><span class="badge badge-success">已完成</span></td>
                <td><button class="btn btn-primary btn-sm" onclick="API.downloadChapterDocx(${doc.chapter}, ${doc.version})">下载</button>
                    <button class="btn btn-ghost btn-sm" style="color:#cf1322" onclick="deleteDocument(${doc.chapter}, ${doc.version})">删除</button></td>
            </tr>
        `).join('');
    } catch (error) {
        console.warn('[REIT-AI] 加载文档列表失败:', error.message);
    }
}

/** 工具栏“生成该文档”：先静默自动保存编辑区，再渲染当前内容为新版本 Word，不触发下载 */
async function generateChapterDoc(n) {
    try {
        const ok = await saveChapter(true);
        if (!ok) return;
        const r = await API.generateDocument(n);
        showToast(`已生成新版本：${r.filename}`);
    } catch (e) {
        showToast(e.message || '生成失败', 'error');
    }
}

/** 删除某章指定版本的正式文档（二次确认，业务语言） */
async function deleteDocument(chapter, version) {
    if (!confirm(`确定删除第${chapter}章 v${version} 版本文档吗？\n删除后不可恢复，其余版本不受影响。`)) return;
    try {
        await API.deleteDocument(chapter, version);
        showToast(`已删除第${chapter}章 v${version} 版本`);
        loadDocuments();
    } catch (e) {
        showToast(e.message || '删除失败');
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
            // 项目创建完立刻进入上传文案；按“已传字节”估算剩余时间（第一个文件没传完也能估），每秒刷新
            const totalBytes = files.reduce((s, f) => s + (f.size || 0), 0);
            let doneBytes = 0, curBatchBytes = 0, lastDone = 0;
            let curBatch = [];  // 当前正在传的批次，用于按字节推算已完成的文件数
            const fmtBytes = (b) => b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB';
            const renderUploadProgress = () => {
                const elapsed = (Date.now() - t0) / 1000;
                const sent = doneBytes + curBatchBytes;
                const speed = elapsed > 2 ? sent / elapsed : 0;  // 字节/秒
                const eta = (speed > 1024 && totalBytes > sent) ? (totalBytes - sent) / speed : null;
                // 批内按字节顺序推算已传完的文件数，让计数跟着字节一起动（不必等整批结束）
                let acc = 0, inBatchDone = 0;
                for (const f of curBatch) {
                    if (acc + (f.size || 0) <= curBatchBytes) { acc += f.size || 0; inBatchDone++; }
                    else break;
                }
                const doneCount = Math.min(files.length, lastDone + inBatchDone);
                const pct = totalBytes ? Math.min(95, 5 + 90 * sent / totalBytes) : 5 + 90 * doneCount / files.length;
                const tip = eta === null
                    ? `｜已用 ${_fmtDur(elapsed)}，正在估算时间…`
                    : `｜已用 ${_fmtDur(elapsed)}，预计还需 ${_fmtDur(eta)}`;
                setProgress(`正在上传申报材料（第 ${doneCount}/${files.length} 个文件）${fmtBytes(sent)}${totalBytes ? ' / ' + fmtBytes(totalBytes) : ''}…${tip}`, pct);
            };
            renderUploadProgress();
            const ticker = setInterval(renderUploadProgress, 1000);
            try {
                // 按大小分批：单批 ≤40MB 且 ≤BATCH 个文件（小批快传，计数/预估刷新更频繁）
                const MAX_BATCH_BYTES = 40 * 1024 * 1024;
                let idx = 0;
                while (idx < files.length) {
                    let j = idx, bsize = 0;
                    while (j < files.length && (j === idx || (bsize + (files[j].size || 0) <= MAX_BATCH_BYTES && j - idx < BATCH))) {
                        bsize += files[j].size || 0; j++;
                    }
                    const batch = files.slice(idx, j);
                    const to = j;
                    curBatch = batch;
                    curBatchBytes = 0;
                    const result = await API.uploadMaterials(batch, (p) => { curBatchBytes = p.loaded || 0; });
                    doneBytes += batch.reduce((s, f) => s + (f.size || 0), 0);
                    curBatchBytes = 0;
                    lastDone = to;
                    uploaded += (result.uploaded || []).length;
                    skipped += (result.skipped || []).length;
                    skippedNames = skippedNames.concat(result.skipped || []);
                    renderUploadProgress();
                    idx = j;
                }
                setProgress('上传完成', 100);
                showToast(`完成：已上传 ${uploaded} 份材料${skipped ? `，${skipped} 个不支持类型的文件已跳过：${skippedNames.slice(0, 3).join('、')}${skippedNames.length > 3 ? ' 等' : ''}` : ''}`);
            } catch (ue) {
                showToast('项目已创建，但部分材料上传失败：' + (ue.message || '') + '；可到「系统设置 → 申报材料」重传', 'error');
            } finally {
                clearInterval(ticker);
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
    loadMaterialsUI();  // 面板默认收起：此处只刷新标题计数/设置页，展开时 toggle 会再拉一次最新列表
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

/** 编辑项目（改名）：与同事版“进入/编辑/删除”三按钮布局对齐，编辑弹改名框 */
function openEditProject(projectId) {
    const proj = (_projectsCache || []).find(p => p.id === projectId);
    if (!proj) { showToast('未找到项目信息，请刷新列表后重试', 'warning'); return; }
    let m = document.getElementById('modalEditProject');
    if (!m) {
        m = document.createElement('div');
        m.id = 'modalEditProject';
        m.className = 'modal-overlay';
        m.innerHTML = `
            <div class="modal" style="width:440px">
                <div class="modal-header"><span class="modal-title">项目改名</span><button class="modal-close" onclick="closeModal('modalEditProject')">✕</button></div>
                <div class="modal-body">
                    <input type="text" id="editProjectName" class="form-input" maxlength="100" placeholder="请输入项目名称">
                </div>
                <div class="modal-footer">
                    <button class="btn btn-ghost" onclick="closeModal('modalEditProject')">取消</button>
                    <button class="btn btn-primary" id="editProjectSave">保存</button>
                </div>
            </div>`;
        document.body.appendChild(m);
        m.addEventListener('click', (e) => { if (e.target === m) closeModal('modalEditProject'); });
        m.querySelector('#editProjectSave').addEventListener('click', async () => {
            const name = (document.getElementById('editProjectName').value || '').trim();
            if (!name) { showToast('项目名称不能为空', 'warning'); return; }
            try {
                await API.updateProject(m.dataset.pid, { name });
                showToast('已改名');
                closeModal('modalEditProject');
                await loadOverviewData();
                updateProjectHeaderBar();
            } catch (e) {
                showToast('改名失败：' + (e.message || e), 'error');
            }
        });
        m.querySelector('#editProjectName').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') m.querySelector('#editProjectSave').click();
        });
    }
    m.dataset.pid = projectId;
    m.querySelector('#editProjectName').value = proj.name || '';
    openModal('modalEditProject');
    setTimeout(() => {
        const el = document.getElementById('editProjectName');
        el.focus(); el.select();
    }, 50);
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
    _initAutosave();
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
/* 侧栏一键收起：状态存 localStorage，下次打开保持 */
(function () {
    const btn = document.getElementById('sidebarToggle');
    if (!btn) return;
    if (localStorage.getItem('reit_sidebar_collapsed') === '1') document.body.classList.add('sidebar-collapsed');
    btn.addEventListener('click', () => {
        const collapsed = document.body.classList.toggle('sidebar-collapsed');
        localStorage.setItem('reit_sidebar_collapsed', collapsed ? '1' : '0');
    });
})();

/* ===================== 封面编辑器 ===================== */
let _coverReady = false;
let _coverState = null;
let _coverLogoUrls = {};   // role -> objectURL（无 cookie 鉴权，<img> 不能直接带 token，fetch blob 转换）
let _coverUploadRole = null;

// 4 个 logo 角色（顺序 = 封面从上到下 / 底部从左到右）
const _COVER_ROLES = [
    { key: 'issuer', label: '发行人', hint: '（原始权益人）', pos: '标题下方' },
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
          <span class="t">${ICN.image} 编辑封面</span>
          <button class="x" id="coverClose" title="关闭">✕</button>
        </div>
        <div class="cover-body">
          <div class="cover-form" id="coverForm"></div>
          <div class="cover-prev"><div class="cv-page" id="coverPreview"></div></div>
        </div>
        <div class="cover-foot">
          <button class="btn btn-ghost btn-sm" id="coverDownload">${ICN.download} 下载封面Word</button>
          <button class="btn btn-primary btn-sm" id="coverSave">${ICN.save} 保存</button>
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
    // logo 图片走 fetch blob → objectURL；释放上一轮的旧 URL
    const old = _coverLogoUrls;
    _coverLogoUrls = {};
    for (const r of _COVER_ROLES) {
        if (_coverState.logos[r.key] && _coverState.logos[r.key].has) {
            try { _coverLogoUrls[r.key] = URL.createObjectURL(await API.coverLogoBlob(r.key)); } catch (e) { /* 失败显示占位 */ }
        }
    }
    Object.values(old).forEach(u => { try { URL.revokeObjectURL(u); } catch (e) { /* 忽略 */ } });
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
        const thumb = (has && _coverLogoUrls[r.key]) ? `<img src="${_coverLogoUrls[r.key]}">` : '<span class="ph">未上传</span>';
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
    const issuer = (issuerHas && _coverLogoUrls.issuer)
        ? `<img src="${_coverLogoUrls.issuer}">`
        : '<div class="cv-miss">发行人 logo（待上传）</div>';
    const ooLines = (s.originators || []).map(x => `<div>${_coverEsc(x)}</div>`).join('');
    let bottom = '';
    ['fund_manager', 'plan_manager', 'advisor'].forEach(k => {
        const has = s.logos[k] && s.logos[k].has;
        bottom += `<div class="slot">${(has && _coverLogoUrls[k])
            ? `<img src="${_coverLogoUrls[k]}">`
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
        closeCoverEditor();   // 保存成功后关闭弹窗
        showToast('封面已保存');
    } catch (err) {
        showToast('保存失败：' + err.message, 'error');
    }
}

async function _coverDownload() {
    try { await API.saveCoverDate((_coverState && _coverState.date_text) || ''); } catch (e) { /* 日期保存失败不阻断下载 */ }
    try {
        await API.downloadCover();
    } catch (err) {
        showToast('下载失败：' + err.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', initApp);
