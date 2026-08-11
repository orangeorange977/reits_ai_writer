/**
 * REIT-AI 法律文件生成系统 - API调用封装
 */

// 跟随当前访问地址：本地打开是 http://127.0.0.1:8000/api；
// 部署到服务器后，用谁的地址访问就自动用谁（http://服务器IP或域名/api），无需再改。
const API_BASE = window.location.origin + '/api';

// 本浏览器当前正在编辑的项目 id（多人各改各的：随请求带给后端，互不覆盖）。
// 存在 localStorage，刷新/重开也记得自己在编辑哪个项目。
let ACTIVE_PROJECT_ID = '';
try { ACTIVE_PROJECT_ID = localStorage.getItem('reitai_activeProjectId') || ''; } catch (e) { }

/**
 * API调用工具类
 */
const API = {
    /** 设置本浏览器“当前项目”（进入/新建项目时调用），随后所有请求都带上它 */
    setActiveProject(id) {
        ACTIVE_PROJECT_ID = id || '';
        try { localStorage.setItem('reitai_activeProjectId', ACTIVE_PROJECT_ID); } catch (e) { }
    },
    getActiveProject() { return ACTIVE_PROJECT_ID; },
    /** 给“图片/下载”这类无法带自定义请求头的 URL 追加 pid 查询参数 */
    withPid(url) {
        if (!ACTIVE_PROJECT_ID) return url;
        return url + (url.includes('?') ? '&' : '?') + 'pid=' + encodeURIComponent(ACTIVE_PROJECT_ID);
    },
    /** multipart（FormData）上传用的请求头：只带项目 id，不设 Content-Type（交给浏览器） */
    _pidHeaders() {
        return ACTIVE_PROJECT_ID ? { 'X-Project-Id': ACTIVE_PROJECT_ID } : {};
    },

    /**
     * 通用请求方法
     * @param {string} endpoint - API端点路径
     * @param {object} options - fetch选项
     * @returns {Promise<any>} 响应数据
     */
    async request(endpoint, options = {}) {
        const url = `${API_BASE}${endpoint}`;
        const defaultHeaders = {
            'Content-Type': 'application/json',
        };
        // 带上本浏览器当前项目，让后端把读写落到这个项目（多人各改各的）
        if (ACTIVE_PROJECT_ID) defaultHeaders['X-Project-Id'] = ACTIVE_PROJECT_ID;

        const config = {
            headers: { ...defaultHeaders, ...options.headers },
            ...options,
        };

        // 如果是下载请求，不设置Content-Type
        if (options._download) {
            delete config.headers['Content-Type'];
            delete config._download;
        }

        try {
            const response = await fetch(url, config);

            if (!response.ok) {
                // 会话过期/未登录：跳到登录页
                if (response.status === 401) {
                    window.location.href = '/login';
                    throw new Error('未登录');
                }
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `请求失败: ${response.status} ${response.statusText}`);
            }

            // 如果是文件下载响应
            if (options._download) {
                return response;
            }

            return await response.json();
        } catch (error) {
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                throw new Error('网络连接失败，请检查后端服务是否启动');
            }
            throw error;
        }
    },

    /**
     * GET请求
     * @param {string} endpoint - API端点
     * @param {object} params - 查询参数
     */
    async get(endpoint, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const url = queryString ? `${endpoint}?${queryString}` : endpoint;
        return this.request(url, { method: 'GET' });
    },

    /**
     * POST请求
     * @param {string} endpoint - API端点
     * @param {object} data - 请求体数据
     */
    async post(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'POST',
            body: JSON.stringify(data),
        });
    },

    /**
     * PUT请求
     * @param {string} endpoint - API端点
     * @param {object} data - 请求体数据
     */
    async put(endpoint, data = {}) {
        return this.request(endpoint, {
            method: 'PUT',
            body: JSON.stringify(data),
        });
    },

    /**
     * DELETE请求
     * @param {string} endpoint - API端点
     */
    async delete(endpoint) {
        return this.request(endpoint, { method: 'DELETE' });
    },

    // ===== 项目管理 =====

    /**
     * 获取项目列表
     * @returns {Promise<Array>} 项目列表
     */
    async getProjects() {
        return this.get('/projects');
    },

    /**
     * 创建新项目
     * @param {string} name - 项目名称
     * @param {string} dataSourcePath - 数据源路径
     * @returns {Promise<object>} 创建的项目信息
     */
    async createProject(name, dataSourcePath) {
        return this.post('/projects', {
            name: name,
            data_source_path: dataSourcePath,
        });
    },

    /**
     * 获取项目详情
     * @param {number} id - 项目ID
     * @returns {Promise<object>} 项目详情
     */
    async getProject(id) {
        return this.get(`/projects/${id}`);
    },

    /**
     * 删除项目
     * @param {number} id - 项目ID
     * @returns {Promise<object>} 删除结果
     */
    async deleteProject(id) {
        return this.delete(`/projects/${id}`);
    },

    // ===== 章节管理 =====

    /**
     * 获取项目所有章节列表
     * @param {number} projectId - 项目ID
     * @returns {Promise<Array>} 章节列表
     */
    async getChapters(projectId) {
        return this.get(`/projects/${projectId}/chapters`);
    },

    /**
     * 获取章节详情
     * @param {number} projectId - 项目ID
     * @param {string} chapterId - 章节ID（如 'chapter1'）
     * @returns {Promise<object>} 章节详情
     */
    async getChapterDetail(projectId, chapterId) {
        return this.get(`/projects/${projectId}/chapters/${chapterId}`);
    },

    /**
     * 更新章节数据
     * @param {number} projectId - 项目ID
     * @param {string} chapterId - 章节ID
     * @param {object} fields - 字段键值对 {field_id: value}
     * @returns {Promise<object>} 更新结果
     */
    async updateChapterData(projectId, chapterId, fields) {
        return this.put(`/projects/${projectId}/chapters/${chapterId}/data`, {
            fields: fields,
        });
    },

    /**
     * 触发章节数据提取
     * @param {number} projectId - 项目ID
     * @param {string} chapterId - 章节ID
     * @returns {Promise<object>} 提取结果
     */
    async extractChapter(projectId, chapterId) {
        return this.post(`/projects/${projectId}/chapters/${chapterId}/extract`);
    },

    // ===== 文档生成 =====

    /**
     * 触发文档生成
     * @param {number} projectId - 项目ID
     * @param {Array|null} chapterIds - 要生成的章节ID列表，null=全部
     * @returns {Promise<object>} 生成启动结果
     */
    async generateDocument(projectId, chapterIds = null) {
        return this.post(`/projects/${projectId}/generate`, {
            chapter_ids: chapterIds,
        });
    },

    /**
     * 获取生成状态
     * @param {number} projectId - 项目ID
     * @returns {Promise<object>} 生成状态 {status, progress_percent, chapters_completed, ...}
     */
    async getGenerateStatus(projectId) {
        return this.get(`/projects/${projectId}/generate/status`);
    },

    /**
     * 下载最新生成的文档
     * @param {number} projectId - 项目ID
     */
    async downloadDocument(projectId) {
        // 直接打开下载链接
        const url = `${API_BASE}/projects/${projectId}/download`;
        const link = document.createElement('a');
        link.href = url;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    },

    /**
     * 获取已生成文档列表
     * @param {number} projectId - 项目ID
     * @returns {Promise<object>} {project_id, documents: [...], total}
     */
    async getDocuments(projectId) {
        return this.get(`/projects/${projectId}/documents`);
    },

    // ===== 文件夹浏览 =====

    /**
     * 浏览本机任意路径（系统设置页选择文件/文件夹用，不限制在数据源目录下）
     * @param {string} path - 要浏览的路径（为空则列出磁盘根目录）
     * @returns {Promise<object>} {current_path, parent_path, items: [...]}
     */
    async browseAnyPath(path = '') {
        const params = {};
        if (path) params.path = path;
        return this.get('/folders/browse-any', params);
    },

    // ===== Skill 执行（Kimi）=====

    /**
     * 启动第 n 章 Kimi 生成（异步）
     * @param {number} n - 章节号 1-7
     * @returns {Promise<object>} {status: 'started', ...}
     */
    async runChapter(n, templatePath = '', materialsPath = '') {
        const params = new URLSearchParams();
        if (templatePath) params.set('template_path', templatePath);
        if (materialsPath) params.set('materials_path', materialsPath);
        const qs = params.toString() ? `?${params.toString()}` : '';
        return this.post(`/skills/chapter/${n}/run${qs}`);
    },

    /**
     * 查询第 n 章生成状态/结果
     * @returns {Promise<object>} {status: 'idle'|'running'|'done'|'error', data, error}
     */
    async getChapterStatus(n) {
        return this.get(`/skills/chapter/${n}/status`);
    },

    /**
     * 一键停止：请求取消第 n 章当前的生成
     * @returns {Promise<object>} {status:'ok', message}
     */
    async stopChapter(n) {
        return this.post(`/skills/chapter/${n}/stop`);
    },

    /**
     * 获取第 n 章可编辑内容（每个子标题一块可读富文本）
     * @returns {Promise<object>} {source:'ready'|'none', sections:[{id,title,html}]}
     */
    async getChapterContent(n, templatePath = '') {
        const params = {};
        if (templatePath) params.template_path = templatePath;
        return this.get(`/skills/chapter/${n}/content`, params);
    },

    /**
     * 保存第 n 章编辑后的内容（回传给 reading skill）
     * @param {number} n
     * @param {Array} sections - [{id, title, html}]
     */
    async saveChapterContent(n, sections) {
        return this.post(`/skills/chapter/${n}/save`, { sections });
    },

    /**
     * 生成第 n 章 Word 并返回预览 HTML（writing skill 写入官方模板对应章节）
     * @param {number} n
     * @param {string} templatePath - 官方模板文件路径（来自系统设置）
     * @returns {Promise<object>} {status, has_content, html, used_template}
     */
    async getChapterPreview(n, templatePath = '') {
        const params = {};
        if (templatePath) params.template_path = templatePath;
        return this.get(`/skills/chapter/${n}/preview`, params);
    },

    /**
     * 下载第 n 章 Word 文件
     */
    downloadChapterDocx(n) {
        const link = document.createElement('a');
        link.href = this.withPid(`${API_BASE}/skills/chapter/${n}/download`);
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    },

    /**
     * 获取摘要表/释义/其他基本信息
     * @returns {Promise<object>} {status, data:{summary_table, glossary, other_info}}
     */
    async getSummary() {
        return this.get('/skills/summary');
    },

    /**
     * 保存摘要表/释义/其他基本信息（持久化到后端）
     * @param {object} data - {summary_table, glossary, other_info}
     * @returns {Promise<object>} {status, message}
     */
    async saveSummary(data) {
        return this.post('/skills/summary/save', data);
    },

    /**
     * 获取可用的 Kimi 模型列表 + 当前所选
     * @returns {Promise<object>} {models:[...], current}
     */
    async getModels() {
        return this.get('/skills/models');
    },

    /**
     * 设置当前使用的 Kimi 模型
     * @param {string} model
     * @returns {Promise<object>} {status, model}
     */
    async setModel(model) {
        return this.post('/skills/model', { model });
    },

    /**
     * AI 辅助写作：把选中文字 + 指令交给 Kimi，返回处理后的文字
     * @param {string} text 选中的原文（可空）
     * @param {string} instruction 用户指令
     * @returns {Promise<object>} {status, result}
     */
    async aiEdit(text, instruction) {
        return this.post('/skills/ai-edit', { text, instruction });
    },

    /**
     * AI 辅助写作（增强版）：指令 + 选中原文 + 素材（粘贴文字/网页链接/上传文件）→ Kimi 综合生成
     * @param {FormData} formData 已装好 instruction/selected_text/pasted_text/urls/files 的表单
     * @returns {Promise<object>} {status, result}
     */
    async aiCompose(formData) {
        // multipart 上传：不要手动设 Content-Type，交给浏览器带 boundary
        const resp = await fetch(`${API_BASE}/skills/ai-compose`, { method: 'POST', body: formData, headers: this._pidHeaders() });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `请求失败: ${resp.status}`);
        }
        return resp.json();
    },

    /**
     * Kimi 聊天入口（多轮对话）：formData 里带 history/message/selected_text/pasted_text/urls/files
     * @returns {Promise<object>} {status, reply}
     */
    async aiChat(formData) {
        const resp = await fetch(`${API_BASE}/skills/ai-chat`, { method: 'POST', body: formData, headers: this._pidHeaders() });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `请求失败: ${resp.status}`);
        }
        return resp.json();
    },

    /** 概览页项目列表那一行：{project_name, industry, stage, chapters_done, chapters_total, last_edit} */
    async getProjectOverview() {
        const r = await this.get('/skills/project-overview');
        return r.data;
    },

    // ===== 多项目（每个项目一套独立的摘要表/章节/封面 JSON） =====
    /** 所有项目 + 当前项目 id：{current, projects:[{id,project_name,industry,chapters_done,...}]} */
    async listSkillProjects() {
        const r = await this.get('/skills/projects');
        return r.data;
    },
    /** 新建项目（会切换为当前项目），返回项目记录 */
    async createSkillProject(body) {
        const r = await this.post('/skills/projects', body);
        return r.data;
    },
    /** 切换当前项目 */
    async setCurrentProject(id) {
        return this.post('/skills/projects/current', { id });
    },
    /** 修改某个项目的名称/路径/模型（不切换当前项目） */
    async updateSkillProject(body) {
        return this.post('/skills/projects/update', body);
    },
    /** 删除某个项目及其数据 */
    async deleteSkillProject(id) {
        return this.delete('/skills/projects/' + encodeURIComponent(id));
    },
    /** 保存项目组自定义的显示名（列表展示用） */
    async saveProjectName(name) {
        return this.post('/skills/project-name', { name });
    },
    /** 服务器端统一设置（模板路径/申报材料路径，全员共用） */
    async getServerSettings() {
        const r = await this.get('/skills/settings');
        return r.data;
    },
    async saveServerSettings(patch) {
        return this.post('/skills/settings', patch);
    },

    // ===== 封面 =====
    /** 取封面状态：标题(自动)、原始权益人(自动)、日期、各 logo 是否已上传 */
    async getCover() {
        const r = await this.get('/skills/cover');
        return r.data;
    },
    /** 保存用户填写的日期 */
    async saveCoverDate(dateText) {
        return this.post('/skills/cover/save', { date_text: dateText });
    },
    /** 上传某角色 logo（role: issuer/fund_manager/plan_manager/advisor） */
    async uploadCoverLogo(role, file) {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch(`${API_BASE}/skills/cover/logo/${role}`, { method: 'POST', body: fd, headers: this._pidHeaders() });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `上传失败: ${resp.status}`);
        }
        return resp.json();
    },
    /** 删除某角色 logo */
    async deleteCoverLogo(role) {
        const resp = await fetch(`${API_BASE}/skills/cover/logo/${role}`, { method: 'DELETE', headers: this._pidHeaders() });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `删除失败: ${resp.status}`);
        }
        return resp.json();
    },
    /** 某角色 logo 的图片 URL（带时间戳防缓存 + 项目 pid） */
    coverLogoUrl(role, bust) {
        return this.withPid(`${API_BASE}/skills/cover/logo/${role}?t=${bust || Date.now()}`);
    },
    /** 下载"只有封面"的 Word */
    downloadCover() {
        const link = document.createElement('a');
        link.href = this.withPid(`${API_BASE}/skills/cover/download`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    },

    /**
     * 获取画图模板列表
     * @returns {Promise<Array>} [{name, label, thumb}]
     */
    async getDiagramTemplates() {
        const r = await this.get('/skills/diagram-templates');
        return r.templates || [];
    },

    /**
     * 获取单个画图模板的 draw.io XML
     * @param {string} name - 模板文件名（不含扩展名）
     * @returns {Promise<object>} {name, xml}
     */
    async getDiagramTemplate(name) {
        return this.get('/skills/diagram-template', { name });
    },

    /**
     * 上传 Excel 导入摘要表/释义/其他基本信息
     * @param {File} file - 用户选择的 xlsx 文件
     * @returns {Promise<object>} {status, data:{summary_table, glossary, other_info}}
     */
    async importSummaryExcel(file) {
        const form = new FormData();
        form.append('file', file);
        const response = await fetch(`${API_BASE}/skills/summary/import-excel`, {
            method: 'POST',
            body: form,
            headers: this._pidHeaders(),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `导入失败: ${response.status}`);
        }
        return response.json();
    },
};
