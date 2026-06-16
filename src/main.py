"""高考志愿智能规划师 - FastAPI HTTP 服务 v2.1

v2.1 安全加固:
- Admin 端点 Token 认证
- Per-IP 速率限制
- IP 防伪造（仅信任 X-Real-IP 白名单来源）
- 健康检查不再调用真实 LLM
- Session 绑定 IP 防枚举
- SSE 心跳按时间间隔发送
- 请求日志中间件
- /download 端点
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
from tools.report import _get_report_filepath
from dotenv import load_dotenv

_project_root = os.path.dirname(_src_dir)
load_dotenv(os.path.join(_project_root, ".env"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-secret-change-me")
TRUSTED_PROXY = os.getenv("TRUSTED_PROXY", "").strip()  # comma-separated trusted proxy IPs

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
# 简单速率限制（Per-IP）
# ============================================================

_RATE_LIMITS: Dict[str, list] = {}  # {ip: [timestamps]}
_RATE_CHAT_LIMIT = 10      # /chat 每分钟 10 次
_RATE_STREAM_LIMIT = 20    # /stream 每分钟 20 次
_RATE_WINDOW = 60          # 窗口 60 秒


def _check_rate_limit(ip: str, limit: int) -> bool:
    """简单滑动窗口速率限制。返回 True=放行, False=超限。"""
    now = time.time()
    window_start = now - _RATE_WINDOW
    timestamps = _RATE_LIMITS.get(ip, [])
    # 清理过期记录
    timestamps = [t for t in timestamps if t > window_start]
    _RATE_LIMITS[ip] = timestamps
    if len(timestamps) >= limit:
        return False
    timestamps.append(now)
    return True


# ============================================================
# 会话管理（内存，绑定 IP）
# ============================================================

# {session_id: {"history": [...], "ip": "..."}}
_sessions: Dict[str, dict] = {}
_MAX_SESSION_HISTORY = 50


def _get_history(session_id: str) -> List[dict]:
    if session_id not in _sessions:
        _sessions[session_id] = {"history": [], "ip": ""}
    return _sessions[session_id]["history"]


def _get_session_ip(session_id: str) -> str:
    return _sessions.get(session_id, {}).get("ip", "")


def _add_message(session_id: str, role: str, content: str):
    if session_id not in _sessions:
        _sessions[session_id] = {"history": [], "ip": ""}
    _sessions[session_id]["history"].append({"role": role, "content": content})
    if len(_sessions[session_id]["history"]) > _MAX_SESSION_HISTORY:
        _sessions[session_id]["history"] = _sessions[session_id]["history"][-_MAX_SESSION_HISTORY:]


# ============================================================
# 支付检查
# ============================================================

def _get_client_ip(request: Request) -> str:
    """提取真实客户端 IP。

    仅当请求来自可信反代（TRUSTED_PROXY 白名单）时，才信任 X-Real-IP 头。
    否则使用直连 IP。
    """
    trusted = set(TRUSTED_PROXY.split(",")) if TRUSTED_PROXY else set()
    client_ip = request.client.host if request.client else "unknown"

    # 只有来自可信代理的请求才读取 X-Real-IP
    if client_ip in trusted:
        x_real = request.headers.get("X-Real-IP", "")
        if x_real:
            return x_real.strip()
    return client_ip


def _check_payment(request: Request, session_id: str) -> Optional[HTTPException]:
    """检查是否可以免费/已付费访问。
    返回 None 表示放行；返回 HTTPException(402) 表示需要付费。
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

app = FastAPI(title="高考志愿智能规划师", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_static_dir = os.path.join(_src_dir, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


# ============================================================
# 请求日志中间件
# ============================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    logger.info(
        "%s %s %d %.2fs",
        request.method, request.url.path, response.status_code, duration
    )
    return response


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
    """健康检查 — 不调用真实 LLM，只检查基础组件状态。"""
    checks = {
        "fastapi": "ok",
        "config": "error",
        "assets": {"NotoSansSC-Regular.ttf": "missing"},
    }
    try:
        cfg = load_config()
        checks["config"] = "ok" if cfg else "error"
    except Exception as e:
        checks["config"] = f"error: {str(e)[:80]}"

    font_path = os.path.join(_project_root, "assets", "NotoSansSC-Regular.ttf")
    checks["assets"]["NotoSansSC-Regular.ttf"] = "ok" if os.path.exists(font_path) else "missing"

    all_ok = all(
        v == "ok" for v in checks.values()
        if isinstance(v, str)
    )
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
    }


@app.post("/chat")
async def chat(request: Request):
    try:
        raw = await request.json()
        body = ChatRequest(**raw)
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=422, detail=str(e)[:500])

    client_ip = _get_client_ip(request)

    # 速率限制
    if not _check_rate_limit(client_ip, _RATE_CHAT_LIMIT):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    # Session 隔离: 检查 session 是否属于此 IP
    sess_ip = _get_session_ip(body.session_id)
    if sess_ip and sess_ip != client_ip:
        raise HTTPException(status_code=403, detail="会话不属于当前客户端")

    # 支付检查
    payment_error = _check_payment(request, body.session_id)
    if payment_error:
        raise payment_error

    try:
        cfg = load_config()
        history = _get_history(body.session_id)
        messages = list(history)
        messages.append({"role": "user", "content": body.message})

        response = chat_completion(
            messages,
            system_prompt=cfg.get("sp", ""),
            temperature=cfg["config"].get("temperature", 0.3),
            max_tokens=cfg["config"].get("max_tokens", 4096),
            stream=False,
        )

        reply = response.choices[0].message.content or ""

        _sessions[body.session_id]["ip"] = client_ip
        _add_message(body.session_id, "user", body.message)
        _add_message(body.session_id, "assistant", reply)

        return {"status": "success", "session_id": body.session_id, "message": reply}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@app.post("/stream")
async def stream(request: Request):
    """流式对话 (SSE)。"""
    try:
        raw = await request.json()
        body = ChatRequest(**raw)
    except (ValidationError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=422, detail=str(e)[:500])

    client_ip = _get_client_ip(request)

    if not _check_rate_limit(client_ip, _RATE_STREAM_LIMIT):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    sess_ip = _get_session_ip(body.session_id)
    if sess_ip and sess_ip != client_ip:
        raise HTTPException(status_code=403, detail="会话不属于当前客户端")

    payment_error = _check_payment(request, body.session_id)
    if payment_error:
        raise payment_error

    async def event_stream():
        try:
            cfg = load_config()
            history = _get_history(body.session_id)
            messages = list(history)
            messages.append({"role": "user", "content": body.message})

            response = chat_completion(
                messages,
                system_prompt=cfg.get("sp", ""),
                temperature=cfg["config"].get("temperature", 0.3),
                max_tokens=cfg["config"].get("max_tokens", 4096),
                stream=True,
            )

            full_reply = ""
            last_heartbeat = time.time()
            for chunk in response:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        content = delta.content
                        full_reply += content
                        yield f"data: {json.dumps({'type': 'ai', 'content': content, 'session_id': body.session_id}, ensure_ascii=False)}\n\n"
                # 每 5 秒发一次心跳（不是每个 chunk）
                now = time.time()
                if now - last_heartbeat >= 5:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now

            _sessions[body.session_id]["ip"] = client_ip
            _add_message(body.session_id, "user", body.message)
            if full_reply:
                _add_message(body.session_id, "assistant", full_reply)

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


@app.get("/download/{report_id}")
async def download_report(report_id: str):
    """下载生成的报告文件。"""
    # 安全检查：防止路径遍历
    if ".." in report_id or "/" in report_id or "\\" in report_id:
        raise HTTPException(status_code=400, detail="非法 report_id")

    filepath = _get_report_filepath(report_id)
    if not filepath or not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="报告不存在或已过期")

    ext = os.path.splitext(filepath)[1].lower()
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == ".docx" else "application/pdf"
    filename = f"Gaokao_Report{ext}"

    return FileResponse(
        filepath,
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
            "free_messages": get_free_quota("_template"),
            "paid_duration_hours": 24,
            "pricing": {
                "chat": {"goods_name": "高考志愿深度分析-单次对话", "amount": "3.00"},
                "report": {"goods_name": "高考志愿综合评估报告", "amount": "9.90"},
            },
        },
    }


@app.get("/payment/status")
async def payment_status(request: Request, session_id: str):
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
    try:
        cfg = load_config()
        pricing = cfg.get("pricing", {}).get("report", {})
        goods_name = pricing.get("goods_name", "高考志愿综合评估报告")
        amount = pricing.get("amount", "9.90")
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
async def admin_reload_config(request: Request):
    """热重载配置 — 需要 ADMIN_TOKEN。"""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized: 需要有效的 ADMIN_TOKEN")

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
    logger.info(f"启动 HTTP 服务 v2.1（联网搜索已开启），端口: {args.port}")
    uvicorn.run("main:app", host="0.0.0.0", port=args.port, reload=False)
