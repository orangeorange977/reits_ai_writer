/**
 * REIT-AI 法律文件生成系统 - API调用封装
 */

const API_BASE = 'http://127.0.0.1:8000/api';

/**
 * API调用工具类
 */
const API = {
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
     * @param {string} packId - 绑定的模板包 ID（不传时后端绑默认包）
     * @returns {Promise<object>} 创建的项目信息
     */
    async createProject(name, dataSourcePath, packId) {
        const body = { name: name, data_source_path: dataSourcePath };
        if (packId) body.pack_id = packId;
        return this.post('/projects', body);
    },

    /**
     * 可用模板包列表（新建项目时的"材料模板"下拉数据源）
     * @returns {Promise<object>} {packs:[{id,name,version,...}], default_id}
     */
    async getPacks() {
        return this.get('/packs');
    },

    /**
     * 模板包详情：manifest + 章节结构（步骤条标题按此渲染）
     * @param {string} packId - 模板包 ID
     * @returns {Promise<object>} {pack:{...}, chapters:[{n,title}]}
     */
    async getPackDetail(packId) {
        return this.get(`/packs/${encodeURIComponent(packId)}`);
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

    // ===== 已生成文档列表（新管线）=====

    /**
     * 获取当前项目已生成的各章 Word 文档列表（文档管理页数据源）
     * @returns {Promise<object>} {documents: [{chapter,title,filename,size_formatted,updated_at}]}
     */
    async getDocuments() {
        return this.get('/skills/documents', { project_id: this._currentProjectId() });
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
     * 当前选中项目 ID（章节内容/摘要表等数据按项目隔离，请求时携带）；
     * 未选项目时返回空串，后端落回默认项目目录。
     */
    _currentProjectId() {
        return (typeof currentProjectId !== 'undefined' && currentProjectId != null)
            ? String(currentProjectId) : '';
    },

    /**
     * 启动第 n 章 Kimi 生成（异步）
     * @param {number} n - 章节号 1-7
     * @returns {Promise<object>} {status: 'started', ...}
     */
    async runChapter(n, templatePath = '', materialsPath = '') {
        const params = new URLSearchParams();
        if (templatePath) params.set('template_path', templatePath);
        if (materialsPath) params.set('materials_path', materialsPath);
        params.set('project_id', this._currentProjectId());
        return this.post(`/skills/chapter/${n}/run?${params.toString()}`);
    },

    /**
     * 查询第 n 章生成状态/结果
     * @returns {Promise<object>} {status: 'idle'|'running'|'done'|'error', data, error}
     */
    async getChapterStatus(n) {
        return this.get(`/skills/chapter/${n}/status`, { project_id: this._currentProjectId() });
    },

    /**
     * 获取第 n 章可编辑内容（每个子标题一块可读富文本）
     * @returns {Promise<object>} {source:'ready'|'none', sections:[{id,title,html}]}
     */
    async getChapterContent(n, templatePath = '') {
        const params = { project_id: this._currentProjectId() };
        if (templatePath) params.template_path = templatePath;
        return this.get(`/skills/chapter/${n}/content`, params);
    },

    /**
     * 保存第 n 章编辑后的内容（回传给最终版 JSON）
     * @param {number} n
     * @param {Array} sections - [{id, title, html}]
     */
    async saveChapterContent(n, sections) {
        const pid = encodeURIComponent(this._currentProjectId());
        return this.post(`/skills/chapter/${n}/save?project_id=${pid}`, { sections });
    },

    /**
     * 生成第 n 章 Word 并返回预览 HTML（写入官方模板对应章节）
     * @param {number} n
     * @param {string} templatePath - 官方模板文件路径（来自系统设置）
     * @returns {Promise<object>} {status, has_content, html, used_template}
     */
    async getChapterPreview(n, templatePath = '') {
        const params = { project_id: this._currentProjectId() };
        if (templatePath) params.template_path = templatePath;
        return this.get(`/skills/chapter/${n}/preview`, params);
    },

    /**
     * 下载第 n 章 Word 文件
     */
    downloadChapterDocx(n) {
        const link = document.createElement('a');
        const pid = encodeURIComponent(this._currentProjectId());
        link.href = `${API_BASE}/skills/chapter/${n}/download?project_id=${pid}`;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    },

    /**
     * 获取当前项目的摘要表/释义/其他基本信息
     * @returns {Promise<object>} {status, data:{summary_table, glossary, other_info}}
     */
    async getSummary() {
        return this.get('/skills/summary', { project_id: this._currentProjectId() });
    },

    /**
     * 保存摘要表/释义/其他基本信息到当前项目（持久化到后端）
     * @param {object} data - {summary_table, glossary, other_info}
     * @returns {Promise<object>} {status, message}
     */
    async saveSummary(data) {
        const pid = encodeURIComponent(this._currentProjectId());
        return this.post(`/skills/summary/save?project_id=${pid}`, data);
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
        const resp = await fetch(`${API_BASE}/skills/ai-compose`, { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `请求失败: ${resp.status}`);
        }
        return resp.json();
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
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `导入失败: ${response.status}`);
        }
        return response.json();
    },
};
