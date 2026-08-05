# REIT-AI 法律文件生成系统（开发者文档）

面向开发者的技术说明。面向非技术用户的使用说明见《使用说明-请先读我.txt》。
重构依据与计划见《问题核实报告.md》《修改步骤.md》。

## 一、项目简介

FastAPI（后端）+ 原生 JS 单页前端，调用 Kimi 大模型，按章节生成 REITs 基金
发改委申报材料（Word 输出）。当前业务（REITs 发改委 2024 版）与引擎耦合较深，
通用化改造按《修改步骤.md》阶段二推进。

## 二、快速启动

**前置**：Python 3.10+；`.env`（参照 `.env.example`，必须配置 `MOONSHOT_API_KEY`）。

```bash
# macOS / Linux
pip install -r requirements.txt
python run_server.py
```

```bat
:: Windows：双击 启动网站.bat（自动建 venv、装依赖、开浏览器）
```

- 页面：http://127.0.0.1:8000
- API 文档（Swagger）：http://127.0.0.1:8000/docs
- 首次启动自动建库（`workspace/app/backend/database/reits.db`）并导入示范项目。

## 三、目录结构

```
├── backend/
│   ├── main.py              # FastAPI 入口（路由注册、静态托管、启动建库）
│   ├── config.py            # 配置（路径、端口、业务路径）
│   ├── database/db.py       # aiosqlite 封装（projects/chapters/metadata/documents）
│   ├── routers/             # API 路由层
│   │   ├── projects.py chapters.py generate.py   # 旧管线：结构化字段提取+整本生成
│   │   ├── skills.py        # 新管线：Kimi 逐章生成/编辑/预览/下载（含任务内存字典）
│   │   ├── enhancements.py  # 增强功能（释义/承诺函/财务/不涉及/基准日/附件）
│   │   └── folders.py       # 本机路径浏览（含 browse-any，上线前必须收敛）
│   ├── services/            # skill_runner（Kimi 工具调用主引擎）、kimi_client、
│   │                        # materials_client、summary_service、tianyancha_client
│   ├── generators/ managers/ mappings/ parsers/  # 旧管线配套（模板/规则/映射/解析）
│   ├── templates/ndrc/*.j2  # 旧管线 Jinja2 模板
│   ├── templates/official/ndrc_2024.docx         # 官方格式文本（导出兜底）
│   └── data/projects/       # 示范项目预置数据
├── frontend/
│   ├── index.html           # 单页骨架（概览/材料生成/文档管理/设置 四页）
│   └── js/                  # api.js(接口封装) app.js(主逻辑) components.js(UI渲染)
│                            # enhancements.js(增强功能六tab) diagram.js(画图)
├── workspace/               # 运行期产物（数据库、输出 docx），不入库
└── ../skills/               # 外层 skills：Kimi 写作规则与产物（见第五节）
```

## 四、架构现状：两套管线并存

```
旧管线（结构化字段式）                     新管线（Kimi 生成式，当前主流程）
projects/chapters/generate.py             routers/skills.py
  ↓ 解析底稿 → 字段提取                      ↓ 读官方模板小标题
generators/ + parsers/ + mappings/        services/skill_runner.py
  ↓ Jinja2 渲染 → docx                       ↓ Kimi 工具调用(读材料/写章节) → ch{n}.json
routers/enhancements.py 的 managers/      在线编辑 → 写入官方模板 → Word 预览/下载
仍挂在旧管线的字段模型上
```

- 前端"发改委材料生成"页的步骤条、章节编辑、Word 预览全部走**新管线**
  （`/api/skills/chapter/{n}/*`）；旧管线保留但不再是主入口。
- 处置计划：阶段二验证新管线完整后整体删除旧管线（见《修改步骤.md》2.6），
  `managers/` 增强功能五件套例外保留。

## 五、外层 skills/ 目录的作用

`skill_runner` 通过 `SKILL.md` 约定与 Kimi 协作，规则与产物都在仓库外的 `skills/`：

| 目录 | 作用 |
|---|---|
| `reits-reading-ch1~7/` | 各章写作要求（SKILL.md）+ 生成产物（ch{n}.json） |
| `reits-writing/` | 排版要求与 Word 装配脚本（assemble.py / web_render.py） |
| `reits-diagrams/` | drawio 画图模板与渲染脚本 |
| `model_setting.json` | 当前所选 Kimi 模型 |
| `summary_saved.json` | 摘要表/释义/其他基本信息（全局唯一一份，待按项目隔离） |

写法约定：每个 skill 目录一个 `SKILL.md`（给模型看的规则），脚本放 `scripts/`，
产物放目录内。`web_render.py` 支持热重载（改后无需重启服务）。
注意：当前规则文件里混有具体项目内容，阶段二（2.3）会清洗并收编为模板包。

## 六、配置与密钥

- `.env`：`MOONSHOT_API_KEY`（Kimi）、`TIANYANCHA_MCP_KEY` 等，绝不入库。
- 系统设置页的模板/材料路径存浏览器 localStorage，后端按路径直读本机文件
  ——单机单用户设计，多用户化改造见《修改步骤.md》阶段三。

## 七、已知约束（上线前必须处理）

1. 无登录认证；`/api/folders/browse-any` 可浏览任意路径（仅本机可用）。
2. CORS 全开；绑定 127.0.0.1；前端 API 地址写死 `http://127.0.0.1:8000/api`。
3. 生成任务状态存内存字典，单进程单用户；多人使用会互相覆盖。
4. 详细清单与修复顺序见《问题核实报告.md》《修改步骤.md》。
