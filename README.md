# REIT-AI 法律文件生成系统（开发者文档）

面向开发者的技术说明。面向非技术用户的使用说明见《用户使用说明书.md》。
历史改造记录（三阶段重构）保存在 git tag `v1.1-bugfix` → `v1.2-packs` → `v1.3-production` 中。

## 一、项目简介

FastAPI（后端）+ 原生 JS 单页前端，调用 Kimi 大模型，按模板包逐章生成 REITs 基金
发改委申报材料（Word 输出）。业务规则、章节结构与写作要求由 `templates-packs/`
模板包承载，引擎与业务解耦。

## 二、快速启动

**前置**：Python 3.12.x；`.env`（参照 `.env.example`，必须配置 `MOONSHOT_API_KEY`）。

```bash
# macOS / Linux
pip install -r requirements.txt
python run_server.py
```

- 页面：http://127.0.0.1:8000
- API 文档（Swagger）：http://127.0.0.1:8000/docs
- 首次启动自动建库（`workspace/app/backend/database/reits.db`）、创建 admin 账号并导入示范项目。

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
services/skill_runner.py（Kimi 工具调用：读材料→写章节）→ workspace/projects/<id>/ch{n}.json
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
