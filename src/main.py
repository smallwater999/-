"""高考志愿智能规划师 - FastAPI HTTP 服务（简化版）

核心改动：
- 去掉 LangGraph + create_agent，直接调 DeepSeek 原生联网搜索
- 会话历史简单列表管理，不再依赖 checkpoint 持久化
- 流式输出直接用 OpenAI SDK 的 stream
"""

import argparse
import json
import logging
import os
import sys
import uuid
import time
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from agent import chat_completion, load_config, reload_config
from payment import (
    generate_payment_needed, get_free_quota, use_free_quota,
    has_paid, mark_as_paid, check_payment_header, reload_merchant_config,
    get_paid_expiry,
)
from dotenv import load_dotenv

_project_root = os.path.dirname(_src_dir)
load_dotenv(os.path.join(_project_root, ".env"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# Pydantic Models
# ============================================================

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="用户消息")
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="会话ID")


# ============================================================
# 会话管理（内存，简单列表）
# ============================================================

# {session_id: [{"role": "user"|"assistant", "content": "..."}, ...]}
_sessions: Dict[str, List[dict]] = {}
_MAX_SESSION_HISTORY = 50  # 保留最近 50 条消息


def _get_history(session_id: str) -> List[dict]:
    if session_id not in _sessions:
        _sessions[session_id] = []
    return _sessions[session_id]


def _add_message(session_id: str, role: str, content: str):
    history = _get_history(session_id)
    history.append({"role": role, "content": content})
    # 限制历史长度（保留 50 条，多余删除最早的）
    if len(history) > _MAX_SESSION_HISTORY:
        # 保留 system 消息 + 最近的
        history[:] = history[-_MAX_SESSION_HISTORY:]


def _trim_history(session_id: str, max_len: int = 50):
    """清理会话历史到最大长度。"""
    history = _get_history(session_id)
    if len(history) > max_len:
        history[:] = history[-max_len:]


# ============================================================
# 支付检查 — 免费额度 + 402 保护
# ============================================================

def _get_client_ip(request: Request) -> str:
    """提取真实客户端 IP（考虑 Nginx 反代）。"""
    # Nginx 设置 X-Real-IP
    x_real = request.headers.get("X-Real-IP", "")
    if x_real:
        return x_real.strip()
    # 兜底: X-Forwarded-For 第一个
    x_fwd = request.headers.get("X-Forwarded-For", "")
    if x_fwd:
        return x_fwd.split(",")[0].strip()
    # 直连
    return request.client.host if request.client else "unknown"


def _require_payment(request: Request, session_id: str) -> Optional[HTTPException]:
    """检查是否可以免费/已付费访问。

    返回 None 表示放行；返回 HTTPException(402) 表示需要付费。

    免费额度: 每 IP 2 条
    付费后: 该 IP 24 小时内无限使用
    """
    client_ip = _get_client_ip(request)

    # 1) 携带有效 payment-proof → 标记该 IP 已付费
    proof = check_payment_header(dict(request.headers))
    if proof:
        mark_as_paid(client_ip, proof)
        logger.info("IP %s 支付凭证有效，24h 内放行", client_ip)
        return None

    # 2) 该 IP 在付费有效期内
    if has_paid(client_ip):
        return None

    # 3) IP 还有免费额度
    if use_free_quota(client_ip):
        remaining = get_free_quota(client_ip)
        logger.info("IP %s 免费额度消耗, 剩余 %d 条", client_ip, remaining)
        return None

    # 4) 免费耗尽且未付费 → 402
    try:
        cfg = load_config()
        pricing = cfg.get("pricing", {}).get("chat_per_session", {})
        goods_name = pricing.get("goods_name", "高考志愿深度分析-24小时无限畅聊")
        amount = pricing.get("amount", "3.00")
    except Exception:
        goods_name = "高考志愿深度分析-24小时无限畅聊"
        amount = "3.00"

    payment_needed = generate_payment_needed(
        goods_name=goods_name,
        amount=amount,
        resource_path="/chat",
    )

    logger.info("IP %s 免费耗尽且未付费，返回 402", client_ip)
    return HTTPException(
        status_code=402,
        detail={
            "code": "10000",
            "msg": "免费体验次数已用完，支付 ¥3.00 即可 24 小时内无限畅聊",
            "success": False,
            "goods_name": goods_name,
            "amount": amount,
        },
        headers={"Payment-Needed": payment_needed},
    )


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(title="高考志愿智能规划师", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(_src_dir, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ============================================================
# Endpoints
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(_static_dir, "chat.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>chat.html not found</h1>"


@app.get("/health")
async def health_check():
    try:
        # 测试 LLM API 是否可达
        cfg = load_config()
        result = chat_completion(
            [{"role": "user", "content": "ping"}],
            temperature=0,
            max_tokens=10,
        )
        llm_ok = hasattr(result, "choices") and len(result.choices) > 0
        return {
            "status": "ok" if llm_ok else "degraded",
            "checks": {
                "fastapi": "ok",
                "llm": "ok" if llm_ok else "error",
                "assets": {"NotoSansSC-Regular.ttf": "ok" if os.path.exists(
                    os.path.join(_project_root, "assets", "NotoSansSC-Regular.ttf")
                ) else "missing"},
            }
        }
    except Exception as e:
        return {"status": "degraded", "checks": {"fastapi": "ok", "llm": f"error: {str(e)[:100]}"}}


@app.post("/chat")
async def chat(request: Request):
    try:
        raw = await request.json()
        body = ChatRequest(**raw)
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=422, detail=str(e)[:500])

    # 支付检查
    payment_error = _require_payment(request, body.session_id)
    if payment_error:
        raise payment_error

    try:
        cfg = load_config()

        # 构建消息列表
        history = _get_history(body.session_id)
        messages = list(history)
        messages.append({"role": "user", "content": body.message})

        # 调 LLM（联网搜索默认开启）
        response = chat_completion(
            messages,
            system_prompt=cfg.get("sp", ""),
            temperature=cfg["config"].get("temperature", 0.3),
            max_tokens=cfg["config"].get("max_tokens", 4096),
            stream=False,
        )

        reply = response.choices[0].message.content or ""

        # 保存历史
        _add_message(body.session_id, "user", body.message)
        _add_message(body.session_id, "assistant", reply)

        _trim_history(body.session_id)

        return {"status": "success", "session_id": body.session_id, "message": reply}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/stream")
async def stream(request: Request):
    """流式对话 (SSE) — 直接用 DeepSeek 流式 API。"""
    try:
        raw = await request.json()
        body = ChatRequest(**raw)
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=422, detail=str(e)[:500])

    # 支付检查
    payment_error = _require_payment(request, body.session_id)
    if payment_error:
        raise payment_error

    async def event_stream():
        try:
            cfg = load_config()

            history = _get_history(body.session_id)
            messages = list(history)
            messages.append({"role": "user", "content": body.message})

            # 流式调用（联网搜索默认开启）
            response = chat_completion(
                messages,
                system_prompt=cfg.get("sp", ""),
                temperature=cfg["config"].get("temperature", 0.3),
                max_tokens=cfg["config"].get("max_tokens", 4096),
                stream=True,
            )

            full_reply = ""
            for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        content = delta.content
                        full_reply += content
                        yield f"data: {json.dumps({'type': 'ai', 'content': content, 'session_id': body.session_id}, ensure_ascii=False)}\n\n"
                # 每 5s 发一次心跳
                yield ": heartbeat\n\n"

            # 保存历史
            _add_message(body.session_id, "user", body.message)
            if full_reply:
                _add_message(body.session_id, "assistant", full_reply)
            _trim_history(body.session_id)

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Stream 错误: {e}")
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/config")
async def get_config():
    cfg = load_config()
    return {
        "model": os.getenv("LLM_MODEL", cfg["config"].get("model")),
        "welcome": cfg.get("welcome_message"),
        "quick_questions": cfg.get("quick_questions"),
        "tools": cfg.get("tools"),
        "payment": {
            "enabled": True,
            "free_messages": 3,
            "paid_duration_hours": 24,
            "pricing": {
                "chat": {"goods_name": "高考志愿深度分析-单次对话", "amount": "3.00"},
                "report": {"goods_name": "高考志愿综合评估报告", "amount": "9.90"},
            },
        },
    }


@app.get("/payment/status")
async def payment_status(request: Request, session_id: str):
    """查询付费状态和剩余免费额度。"""
    client_ip = _get_client_ip(request)
    paid = has_paid(client_ip)
    expiry = get_paid_expiry(client_ip)
    remaining = get_free_quota(client_ip)
    return {
        "session_id": session_id,
        "has_paid": paid,
        "paid_until": expiry,
        "paid_duration_hours": 24,
        "free_remaining": remaining,
    }


@app.get("/buy/report")
async def buy_report(session_id: str):
    """购买志愿评估报告 — 返回 402 Payment-Needed。"""
    try:
        cfg = load_config()
        pricing = cfg.get("pricing", {}).get("report", {})
        goods_name = pricing.get("goods_name", "高考志愿综合评估报告")
        amount = pricing.get("amount", "0.99")
    except Exception:
        goods_name = "高考志愿综合评估报告"
        amount = "9.90"

    payment_needed = generate_payment_needed(
        goods_name=goods_name,
        amount=amount,
        resource_path="/buy/report",
    )
    return JSONResponse(
        status_code=402,
        content={"code": "10000", "msg": "此为付费内容", "success": False},
        headers={"Payment-Needed": payment_needed},
    )


@app.post("/admin/reload-config")
async def admin_reload_config():
    try:
        new_cfg = reload_config()
        reload_merchant_config()
        return {"status": "ok", "message": "配置已重新加载"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:200])


def parse_args():
    parser = argparse.ArgumentParser(description="高考志愿智能规划师")
    parser.add_argument("-p", "--port", type=int, default=int(os.getenv("PORT", "8000")))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    logger.info(f"启动 HTTP 服务（联网搜索已开启），端口: {args.port}")
    uvicorn.run("main:app", host="0.0.0.0", port=args.port, reload=False)
