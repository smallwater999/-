# 🎓 高考志愿智能规划师

基于 AI Agent 的高考志愿填报智能助手，支持联网搜索、个性化分析、报告生成和图表可视化。

## ✨ 功能特性

- **🔍 联网搜索** — 实时查询院校分数线、专业排名、招生政策
- **📊 智能分析** — MBTI 性格评估 + 霍兰德职业兴趣 + Gardner 多元智能
- **📄 报告生成** — 一键生成 PDF/DOCX 格式志愿填报报告
- **📈 图表可视化** — 雷达图、柱状图展示个人能力画像
- **💬 流式对话** — SSE 实时流式输出，体验流畅
- **💰 支付系统** — 支付宝 A2A 协议接入，支持按次/按时长付费
- **🧠 记忆系统** — SQLite 持久化用户会话，跨对话保持上下文

## 🛠 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | FastAPI + Uvicorn |
| AI | DeepSeek V4 (via 火山引擎 Ark API) |
| Agent | LangGraph (可选，checkpoint 持久化) |
| 搜索 | DeepSeek 原生联网搜索 |
| 图表 | Matplotlib |
| 报告 | fpdf2 + python-docx |
| 支付 | 支付宝 A2A 协议 (RSA2-SHA256 签名) |
| 存储 | SQLite + WAL 模式 |

## 📁 项目结构

```
├── src/
│   ├── main.py              # FastAPI 服务入口
│   ├── agent.py             # LLM 客户端（DeepSeek）
│   ├── payment.py           # 支付宝 A2A 支付模块
│   ├── static/
│   │   └── chat.html        # 聊天界面
│   └── tools/
│       ├── search.py        # 搜索工具
│       ├── report.py        # 报告生成（PDF/DOCX）
│       ├── chart.py         # 图表生成（雷达图/柱状图）
│       └── memory.py        # 用户记忆存储
├── config/
│   ├── agent_llm_config.json          # Agent 系统提示词和模型配置
│   └── a2a_merchant.example.json     # 支付宝商户配置模板
├── assets/
│   ├── NotoSansSC-Regular.ttf        # 中文字体
│   └── NotoSansSC-Regular.otf
├── scripts/
│   └── serveo-watchdog.sh            # Serveo 隧道保活
├── docs/
│   └── API.md                        # API 文档
├── start.bat             # Windows 一键启动
├── run.sh                # Linux/Mac 启动脚本
├── requirements.txt      # Python 依赖
└── .env.example          # 环境变量模板
```

## 🚀 快速开始

### 环境要求

- Python 3.10+
- 火山引擎 Ark API Key（或其他 OpenAI 兼容 API）

### Linux / macOS

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/gaokao-agent.git
cd gaokao-agent

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 LLM_API_KEY

# 3. 安装依赖
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. 启动服务
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000

# 5. 打开浏览器访问 http://localhost:8000
```

### Windows

```cmd
# 解压后双击 start.bat，自动完成安装和启动
# 或手动执行：
python -m venv win_venv
win_venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
```

## ⚙️ 配置说明

### 环境变量（.env）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM API 密钥 | 必填 |
| `LLM_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名称 | `deepseek-chat` |
| `PORT` | 服务端口 | `8000` |

### Agent 配置

编辑 `config/agent_llm_config.json` 可自定义：
- 系统提示词（含分省录取规则）
- 模型参数（temperature、max_tokens）
- 快捷问题和欢迎语
- 定价策略

### 支付接入

1. 注册支付宝商户并获取 AppId、SellerId、私钥
2. 将 `config/a2a_merchant.example.json` 复制为 `a2a_merchant.json`
3. 填入商户信息，私钥放入 `config/a2a_private_key.pem`

## 📡 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 聊天界面 |
| GET | `/health` | 健康检查 |
| POST | `/chat` | 同步对话 |
| POST | `/stream` | SSE 流式对话 |
| GET | `/config` | 获取配置 |
| GET | `/payment/status` | 查询付费状态 |
| POST | `/admin/reload-config` | 热重载配置 |

详见 [docs/API.md](docs/API.md)

## 📝 License

MIT License - 详见 [LICENSE](LICENSE) 文件
