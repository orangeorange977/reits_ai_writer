/**
 * REIT-AI 法律文件生成系统 - API调用封装
 */

// 同源部署：前端由后端同端口托管，API 走相对路径，不写死主机/端口（步骤 3.1）
const API_BASE = '/api';

// ===== 登录态（步骤 3.2）：token 存 sessionStorage，关闭标签页即失效 =====
const AuthToken = {
    get: () => sessionStorage.getItem('reitai_token') || '',
    set: (token) => sessionStorage.setItem('reitai_token', token),
    clear: () => sessionStorage.removeItem('reitai_token'),
    headers: () => {
        const t = AuthToken.get();
        return t ? { 'Authorization': 'Bearer ' + t } : {};
    },
};

/** 收到 401：登录态失效，清 token 并弹出登录层 */
function handleUnauthorized(detail) {
    AuthToken.clear();
    if (typeof showLoginOverlay === 'function') {
        showLoginOverlay(detail || '登录已过期，请重新登录');
    }
}

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
            ...AuthToken.headers(),
        };

        const config = {
            headers: { ...defaultHeaders, ...options.headers },
            ...options,
        };

        // 如果是下载请求，不设置Content-Type
        const isDownload = options._download;
        if (isDownload) {
            delete config.headers['Content-Type'];
            delete config._download;
        }

        // FormData 上传（multipart）：Content-Type 交给浏览器自动带 boundary
        if (options.body instanceof FormData) {
            delete config.headers['Content-Type'];
        }

        try {
            const response = await fetch(url, config);

            if (response.status === 401 && !endpoint.startsWith('/auth/login')) {
                // 登录接口的 401 是"密码错误"，由后端 detail 展示；其余 401 才是登录态失效
                handleUnauthorized();
                throw new Error('未登录或登录已过期');
            }

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `请求失败: ${response.status} ${response.statusText}`);
            }

            // 如果是文件下载响应
            if (isDownload) {
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

    // ===== 申报材料上传（步骤 3.4：上传模式替代本机路径）=====

    /**
     * 上传申报材料（多文件，支持 zip 自动解压）到当前项目
     * @param {FileList|File[]} files - 选中的文件
     * @returns {Promise<object>} {uploaded, extracted_from_zip, skipped}
     */
    async uploadMaterials(files) {
        const form = new FormData();
        // 文件夹上传时携带相对路径，后端按目录结构落盘
        for (const f of files) form.append('files', f, f.webkitRelativePath || f.name);
        const pid = encodeURIComponent(this._currentProjectId());
        return this.request(`/projects/${pid}/materials`, { method: 'POST', body: form });
    },

    /**
     * 列出当前项目已上传的申报材料（递归，含多级子文件夹）
     * @returns {Promise<object>} {total_files, total_size, files:[{path,size}]}
     */
    async listMaterials() {
        const pid = encodeURIComponent(this._currentProjectId());
        return this.get(`/projects/${pid}/materials`);
    },

    /**
     * 预览材料原文（“依据”标注点击后核对出处用）
     * @returns {Promise<object>} {filename, path, text}
     */
    async previewMaterial(path) {
        const pid = encodeURIComponent(this._currentProjectId());
        return this.get(`/projects/${pid}/materials/preview`, { path });
    },

    /**
     * 清空当前项目的全部申报材料
     */
    async clearMaterials() {
        const pid = encodeURIComponent(this._currentProjectId());
        return this.delete(`/projects/${pid}/materials`);
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
     * 浏览数据源文件夹（限定在服务器 DATA_SOURCE_BASE 内，步骤 3.3）
     * @param {string} path - 要浏览的路径（为空则从数据源根目录开始）
     * @returns {Promise<object>} {current_path, parent_path, items: [...]}
     */
    async browseFolder(path = '') {
        const params = {};
        if (path) params.path = path;
        return this.get('/folders/browse', params);
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
     * 启动第 n 章 Kimi 生成（异步）；材料目录由后端自动解析到项目上传目录（步骤 3.4）
     * @param {number} n - 章节号
     * @returns {Promise<object>} {status: 'started', ...}
     */
    async runChapter(n) {
        const params = new URLSearchParams();
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
     * 获取第 n 章可编辑内容（每个子标题一块可读富文本）；模板骨架由后端自动回退到材料包内置模板
     * @returns {Promise<object>} {source:'ready'|'none', sections:[{id,title,html}]}
     */
    async getChapterContent(n) {
        return this.get(`/skills/chapter/${n}/content`, { project_id: this._currentProjectId() });
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
     * 生成第 n 章 Word 并返回预览 HTML（写入官方模板对应章节；模板自动回退材料包内置）
     * @param {number} n
     * @returns {Promise<object>} {status, has_content, html, used_template}
     */
    async getChapterPreview(n) {
        return this.get(`/skills/chapter/${n}/preview`, { project_id: this._currentProjectId() });
    },

    /**
     * 下载第 n 章 Word 文件（fetch blob 方式，才能携带登录 token）
     */
    async downloadChapterDocx(n) {
        const pid = encodeURIComponent(this._currentProjectId());
        const resp = await this.request(`/skills/chapter/${n}/download?project_id=${pid}`, { _download: true });
        const blob = await resp.blob();
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `ch${n}_output.docx`;
        link.style.display = 'none';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
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
        const resp = await fetch(`${API_BASE}/skills/ai-compose`, {
            method: 'POST', body: formData, headers: AuthToken.headers(),
        });
        if (resp.status === 401) { handleUnauthorized(); throw new Error('未登录或登录已过期'); }
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
            headers: AuthToken.headers(),
        });
        if (response.status === 401) { handleUnauthorized(); throw new Error('未登录或登录已过期'); }
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `导入失败: ${response.status}`);
        }
        return response.json();
    },

    // ===== 登录认证（步骤 3.2）=====

    /** 登录：成功返回 {token, username, role, must_change_password} */
    async login(username, password) {
        return this.post('/auth/login', { username, password });
    },

    /** 当前登录用户信息 */
    async getMe() {
        return this.get('/auth/me');
    },

    /** 修改当前用户密码 */
    async changePassword(oldPassword, newPassword) {
        return this.post('/auth/change-password', { old_password: oldPassword, new_password: newPassword });
    },
};
