# 高考志愿智能规划师 - API 接口文档

## 服务地址

```
BASE_URL = https://78d055399604812c-140-143-182-222.serveousercontent.com
```

---

## 1. 会话管理

所有多轮对话通过 `session_id` 区分会话。同一 `session_id` 内的上下文会被 Agent 记住（测评进度、省份信息、志愿方案等）。

- 前端生成一个 UUID 作为 `session_id`
- 同一会话的所有请求带上相同的 `session_id`
- 需要开启新会话时，更换 `session_id` 即可

---

## 2. 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/stream` | **流式对话（推荐）** — SSE 逐字推送 |
| `POST` | `/chat` | 同步对话 — 等待完整回复后返回 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/config` | 获取 Agent 配置信息 |
| `GET` | `/` | 官网可内嵌的可视化对话页面 |

---

## 3. POST /stream（流式对话 — 推荐）

### 请求

```http
POST /stream HTTP/1.1
Content-Type: application/json
```

```json
{
  "message": "我孩子在北京，650分，想学计算机",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户输入的消息 |
| `session_id` | string | 否 | 会话 ID，不传则自动生成 |

### 响应（SSE 流）

```
Content-Type: text/event-stream
```

每条数据格式：

```
data: {"type":"ai","content":"你好","session_id":"550e84..."}

data: [DONE]
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | 固定为 `"ai"` |
| `content` | string | **增量**文本片段（需前端累加），可能包含 Markdown 格式 |
| `session_id` | string | 当前会话 ID |

### 前端集成示例

```javascript
async function chat(message, sessionId) {
  const resp = await fetch('https://BASE_URL/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fullText = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const payload = line.slice(6).trim();
      if (payload === '[DONE]') return fullText;

      try {
        const { content } = JSON.parse(payload);
        fullText += content;
        // 实时渲染 Markdown
        updateUI(renderMarkdown(fullText));
      } catch (e) {}
    }
  }
  return fullText;
}
```

### 响应内容格式

Agent 返回 **Markdown** 格式文本，支持：

- `**粗体**` / `*斜体*`
- `# ## ###` 标题
- `- ` 无序列表 / `1. ` 有序列表
- `---` 分割线
- `[工具调用: xxx]` 工具调用标记（前端可渲染为 badge）

---

## 4. POST /chat（同步对话）

### 请求

```http
POST /chat HTTP/1.1
Content-Type: application/json
```

```json
{
  "message": "你好",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 响应

```json
{
  "status": "success",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "你好！👋 我是高考志愿填报专家助手。在正式开始之前，我需要先了解：**孩子的学籍在哪个省市？**"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | `"success"` 或 `"cancelled"` |
| `session_id` | string | 当前会话 ID |
| `message` | string | AI 的完整回复（Markdown 格式） |

### 接口限制

- 超时时间：**900 秒（15 分钟）**
- 适合需要等待完整结果的场景
- 长回复时用户等待时间较长，建议优先用 `/stream`

---

## 5. GET /health（健康检查）

### 请求

```http
GET /health HTTP/1.1
```

### 响应

```json
{
  "status": "ok",
  "message": "高考志愿智能规划师服务运行中"
}
```

用途：负载均衡健康探测、服务可用性监控。

---

## 6. GET /config（Agent 配置）

### 请求

```http
GET /config HTTP/1.1
```

### 响应

```json
{
  "model": "deepseek-v4-flash",
  "welcome": "你好！我是高考志愿智能规划师。输入\"高考\"即可启动。",
  "quick_questions": [
    "我孩子高三，怎么开始？",
    "什么是五阶段流程？",
    "需要准备什么信息？"
  ],
  "tools": [
    "web_search",
    "memory_save",
    "memory_load",
    "generate_report",
    "generate_radar_chart"
  ]
}
```

用途：官网拉取欢迎语、快捷问题、可用能力列表进行展示。

---

## 7. 官网集成架构参考

```
┌─────────────────┐     SSE Stream      ┌──────────────────┐
│   公司官网前端    │ ◄────────────────── │  高考志愿 Agent   │
│  (用户聊天 UI)   │ ──────────────────► │  (本项目服务)     │
│                 │   POST /stream      │                  │
│                 │   {message,         │  端口 8000        │
│                 │    session_id}      │                  │
└─────────────────┘                     └──────────────────┘
```

### 集成要点

1. **会话 ID 由前端生成并维护**（`crypto.randomUUID()`），存储在 localStorage 或内存中
2. **流式渲染**：收到的每个 `content` 是增量，需要前端累加后渲染 Markdown
3. **Markdown 解析**：建议用 `marked.js` 或类似库做安全渲染
4. **工具调用提示**：`[工具调用: web_search]` 可在 UI 中显示为 "正在搜索院校数据..."
5. **超时处理**：15 分钟内无响应则前端主动断开重试

---

## 8. 完整调用示例

### cURL（流式）

```bash
curl -N -X POST https://78d055399604812c-140-143-182-222.serveousercontent.com/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"我孩子北京650分想学计算机","session_id":"test-001"}'
```

### cURL（同步）

```bash
curl -X POST https://78d055399604812c-140-143-182-222.serveousercontent.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","session_id":"test-001"}'
```

### JavaScript / TypeScript（推荐）

```typescript
interface StreamChunk {
  type: 'ai';
  content: string;
  session_id: string;
}

class GaokaoAgent {
  private baseUrl: string;
  private sessionId: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    this.sessionId = crypto.randomUUID();
  }

  async chat(message: string): Promise<string> {
    const resp = await fetch(`${this.baseUrl}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: this.sessionId }),
    });

    const reader = resp.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let fullText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      for (const line of buffer.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') return fullText;
        try {
          const chunk: StreamChunk = JSON.parse(data);
          fullText += chunk.content;
        } catch {}
      }
    }
    return fullText;
  }

  reset() {
    this.sessionId = crypto.randomUUID();
  }
}

// 使用
const agent = new GaokaoAgent('https://78d055399604812c-140-143-182-222.serveousercontent.com');
const reply = await agent.chat('我孩子在北京，650分，想学计算机');
```

### Python

```python
import httpx
import uuid
import json

class GaokaoAgent:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session_id = str(uuid.uuid4())

    async def chat(self, message: str) -> str:
        async with httpx.AsyncClient(timeout=900) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/stream",
                json={"message": message, "session_id": self.session_id},
            ) as resp:
                full_text = ""
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:].strip()
                        if data == "[DONE]":
                            return full_text
                        try:
                            chunk = json.loads(data)
                            full_text += chunk.get("content", "")
                        except json.JSONDecodeError:
                            pass
                return full_text

    def reset(self):
        self.session_id = str(uuid.uuid4())
```

---

## 9. 错误处理

| HTTP 状态码 | 说明 | 处理建议 |
|-------------|------|----------|
| `200` | 成功 | - |
| `400` | 请求参数错误（message 为空等） | 前端校验非空 |
| `500` | 服务内部错误 | 提示用户重试，上报 error |
| SSE 流中断 | 网络问题或超时 | 前端检测 `done` 状态，断线重连 |
