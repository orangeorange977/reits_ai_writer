# REIT-AI 法律文件生成系统（开发者文档）

面向开发者的技术说明。面向非技术用户的使用说明见《用户使用说明书.md》。
历史改造记录（三阶段重构）保存在 git tag `v1.1-bugfix` → `v1.2-packs` → `v1.3-production` 中。

## 一、项目简介

FastAPI（后端）+ 原生 JS 单页前端，调用 Kimi 大模型，按模板包逐章生成 REITs 基金
发改委申报材料（Word 输出）。业务规则、章节结构与写作要求由 `templates-packs/`
模板包承载，引擎与业务解耦。第一章第一节、第二章第三节已实现“人工输入 → 一文件一
Markdown 底稿知识库 → 可解释的数据中间层 → 小节成稿 → 报告级 AI 审核”的演示闭环。

## 二、快速启动

**前置**：Python 3.9～3.12；`.env` 参照 `.env.example`。文本生成可配置 DeepSeek 或
Moonshot；扫描页视觉精读必须配置 `MOONSHOT_API_KEY`。没有视觉 Key 时会回退本地 OCR，
但复杂财务表格只展示 OCR 原文，不会猜测金额。

```bash
# macOS：首次安装本地 OCR
brew install tesseract tesseract-lang

# 创建隔离环境并启动
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，至少设置 ADMIN_INIT_PASSWORD、JWT_SECRET；需要 AI 时配置对应 Key
python run_server.py
```

- 页面：http://127.0.0.1:8000
- API 文档（Swagger）：http://127.0.0.1:8000/docs
- 首次启动自动建库（`workspace/app/backend/database/reits.db`）、创建 admin 账号并导入示范项目。

本地开发服务默认只监听 `127.0.0.1`。代码改动后 `run_server.py` 不会热重载，需要重启。

### 两节业务演示（业务自测顺序）

1. 新建项目，在“申报材料”中上传完整证明材料文件夹；以下两份人工输入也必须一起上传，
   放在任意层级均可：`润泽摘要表.docx`、`润泽项目摘要表格.docx`。
2. 打开“数据工作台 → 人工输入”，确认系统已读到两份 Word。它们独立保存，不进入 AI
   抽取字段库。
3. 进入项目后默认打开“数据提取”。上传不会自动调用 OCR、模型、天眼查或联网搜索；业务
   确认材料后点击“提取数据”，后台才建立一文件一 Markdown、精读目标页并抽取事实。
4. 在“数据与规则”检查每个字段的当前值、原文件/页码、来源角色、提取策略和说明。来源文件
   支持按文件名搜索；保存规则后点“重新提取数据”，新规则才执行。字段人工修订值优先于抽取值。
5. 步骤条当前只注册两个可生成小节：`1.1 （一）项目概况`、`2.3 （三）发起人（原始权益人）
   情况`。每个大章标题行可一次生成本章全部已配置小节；也可点击具体小节定点重生成。两种入口都执行
   小节 Skill，不再运行旧的整章 Agent；第一节的两张表直接来自人工输入。
6. 天眼查用于实际控制人和企业画像，公开网络检索用于已发行 REITs/近十二个月退回情况；
   查询时间、原始返回、权威网页链接或失败原因保存在中间层。联系人信息绝不联网搜索。
7. 打开“报告审核”。小节生成后会后台自动审核；也可手动运行全报告规则检查或 AI 审核。
   审核结果只提示，不阻止 Word 导出，最终 Word 不带系统内溯源标记。

项目运行期文件：

- `manual_inputs.json`：两份业务手填 Word 的结构化快照；
- `knowledge/documents/<文档ID>/document.md`：一份源文件对应一份完整 Markdown；
- `knowledge/documents/<文档ID>/pages/`：内部页级 OCR/视觉缓存；
- `data_foundation.json`：AI 抽取事实、规则、来源、候选和人工覆盖值；
- `foundation_rule_overrides.json`：业务在当前项目修改后的提取规则；
- `report_audit.json`：生成报告的规则/AI 审核结果。

默认规则编译件位于 `templates-packs/reits-ndrc-2024/data-foundation/rules.json`；两个业务
Know-how 编译后的独立 Skill 位于 `templates-packs/reits-ndrc-2024/section-skills/`。

**生产部署（Docker）**：见 `deploy/`（Dockerfile、docker-compose.yml、nginx.conf、backup.sh），
`docker compose up -d --build` 一条命令起全站（app + nginx 反代）。

## 三、目录结构

```
├── backend/
│   ├── main.py              # FastAPI 入口（路由注册、静态托管、JWT 鉴权中间件、启动建库）
│   ├── config.py            # 配置（路径、端口、限流、token 有效期，环境变量容错）
│   ├── database/db.py       # aiosqlite 封装（users/projects/generation_jobs 等）
│   ├── routers/             # API 路由层
│   │   ├── auth.py          # 登录/改密/me（JWT、失败锁定、首登强制改密）
│   │   ├── skills.py        # 主流程：Kimi 逐章生成/编辑/预览/下载/文档管理（任务状态落库）
│   │   ├── projects.py      # 项目 CRUD（按账号隔离）
│   │   ├── enhancements.py  # 增强功能（释义/承诺函/财务/不涉及/基准日/附件）
│   │   ├── packs.py         # 模板包列表与详情
│   │   └── folders.py       # 数据目录内路径浏览（已收敛，禁越界）
│   ├── services/            # skill_runner（Kimi 工具调用主引擎）、kimi_client、
│   │                        # materials_client、summary_service、auth、pack_service
│   ├── managers/ mappings/  # 增强功能管理器与业务映射配置
│   ├── templates/official/ndrc_2024.docx         # 官方格式文本（Word 渲染基底）
│   └── data/projects/       # 示范项目预置数据
├── frontend/
│   ├── index.html           # 单页骨架（概览/材料生成/文档管理/设置）
│   └── js/                  # api.js(接口封装) app.js(主逻辑) components.js(UI渲染)
│                            # enhancements.js(增强功能六tab) diagram.js(画图)
├── templates-packs/         # 模板包：章节结构、写作要求、官方 Word 模板（权威来源）
├── deploy/                  # 生产部署：nginx.conf、backup.sh（compose/Dockerfile 在根目录）
└── workspace/               # 运行期产物（数据库、项目材料、ch{n}.json、输出 docx），不入库
```

## 四、架构（新管线单一主流程）

旧管线（generators/、结构化字段式整本生成）已整体删除，当前为单一管线：

```
templates-packs/（章节结构+写作要求+官方模板）
  ↓
两份人工 Word → manual_input_service.py（独立人工输入层）
  ↓
源文件 → document_pipeline_service.py（一文件一 Markdown；页级 OCR/视觉缓存）
  ↓
data-foundation/rules.json → data_foundation_service.py（可解释抽取/溯源/冲突/覆盖）
  ↓
services/skill_runner.py（Kimi 工具调用 + 底座确定性小节）→ workspace/projects/<id>/ch{n}.json
  ↓
report_audit_service.py（生成结果审核，不阻断导出）
  ↓
在线编辑（表格/脚注/插图/AI辅助）→ 写入官方模板 → Word 预览/下载
```

- 前端"发改委材料生成"页全部走 `/api/skills/chapter/{n}/*`；
- 生成任务状态持久化到 `generation_jobs` 表，重启/多 worker 不丢；
- 项目数据按 `workspace/projects/<项目ID>/` 物理隔离，API 层按归属校验。

## 五、安全模型（阶段三已上线）

- JWT 登录认证，token 有效期可配（默认 12 小时）；登录失败 5 次锁定 15 分钟；
- 所有 `/api` 路由强制鉴权（登录/改密接口除外）；
- 项目归属校验：跨账号访问一律 404（含路径参数与 query 参数端点）；
- `project_id` 路径净化，防目录穿越；CORS 白名单；AI 接口按账号限流；
- 生产 `.env` 权限 600，密钥不入库、不进镜像、不落日志。

## 六、配置与密钥

- `.env`：`MOONSHOT_API_KEY`（Kimi）、`TIANYANCHA_MCP_KEY` 等，绝不入库（见 `.env.example`）。
- 整数型环境变量（`APP_PORT`、`AI_RATE_LIMIT_PER_MINUTE`、`TOKEN_TTL_HOURS`）非法值回退默认。

## 七、运维

- 生产环境：Docker（app + nginx），健康检查 `/api/health`；
- 每日凌晨 3 点自动备份 `workspace/`（`deploy/backup.sh`，保留 7 天）；
- HTTPS 待域名就绪后叠 certbot（nginx.conf 已预留）。

## 八、自测

```bash
# 不依赖外部 AI 的服务级回归测试
.venv/bin/python -m unittest discover -s tests -v

# Python 语法和未提交补丁检查
.venv/bin/python -m py_compile backend/services/*.py backend/routers/*.py
git diff --check
```

视觉财务抽取会真实调用 Moonshot。“提取数据”会复用来源、规则均未变化的抽取快照；只有
点击“重新提取数据”或材料/规则变化时才需要重新精读，避免对同一份财报反复运行视觉识别。
