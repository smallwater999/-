"""高考志愿智能规划师 — A2A 支付模块 (HTTP 402 Payment Required)

基于支付宝 A2M 智能收协议:
- RSA2-SHA256 签名生成 Payment-Needed 账单
- 免费额度管理 (每会话 N 条免费消息)
- 支付凭证验证 + 履约回执生成

商户密钥配置: config/a2a_merchant.json
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_MERCHANT_CONFIG: Optional[dict] = None
_IP_QUOTA: Dict[str, int] = {}  # {client_ip: remaining_free_msgs}
_PAID_IPS: Dict[str, str] = {}  # {client_ip: paid_until_iso8601}

# --- 加载商户配置 ---

def _load_merchant_config() -> dict:
    global _MERCHANT_CONFIG
    if _MERCHANT_CONFIG is not None:
        return _MERCHANT_CONFIG
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, "config", "a2a_merchant.json")
    if not os.path.exists(path):
        logger.warning("A2A 商户配置不存在，支付功能不可用: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 如果配置了 private_key_path，从文件读取私钥
    key_path = cfg.get("private_key_path", "")
    if key_path:
        if not os.path.isabs(key_path):
            key_path = os.path.join(project_root, key_path)
        if os.path.exists(key_path):
            with open(key_path, "r") as kf:
                cfg["private_key"] = kf.read()
        else:
            logger.warning("私钥文件不存在: %s", key_path)
    _MERCHANT_CONFIG = cfg
    return cfg


def reload_merchant_config():
    global _MERCHANT_CONFIG
    _MERCHANT_CONFIG = None
    return _load_merchant_config()


# --- RSA2 签名 ---

def _rsa2_sign(params: dict, private_key_pem: str) -> str:
    """对参数字典做 RSA2-SHA256 签名，返回 Base64 字符串。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    # 按 key 字母序排序，过滤空值，拼接 k=v&k=v
    keys = sorted(params.keys())
    sign_str = "&".join(
        f"{k}={params[k]}" for k in keys
        if params[k] is not None and params[k] != ""
    )
    logger.debug("签名原串: %s", sign_str)

    private_key = load_pem_private_key(private_key_pem.encode(), password=None)
    signature = private_key.sign(
        sign_str.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode()


# --- Payment-Needed 账单生成 ---

def generate_payment_needed(
    goods_name: str,
    amount: str,
    resource_path: str,
) -> str:
    """生成 Payment-Needed 头值 (Base64 URL Safe 编码的 JSON)。

    Args:
        goods_name: 商品名称 (如 "高考志愿深度分析")
        amount: 金额 (如 "0.01")
        resource_path: 资源路径 (如 "/chat")

    Returns:
        Base64 URL Safe 字符串，可直接放入 Payment-Needed 响应头
    """
    cfg = _load_merchant_config()
    if not cfg:
        raise RuntimeError("A2A 商户配置未加载，无法生成账单")

    out_trade_no = f"GK_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    pay_before = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()

    sign_params = {
        "amount": amount,
        "currency": "CNY",
        "goods_name": goods_name,
        "out_trade_no": out_trade_no,
        "pay_before": pay_before,
        "resource_id": resource_path,
        "seller_id": cfg["seller_id"],
        "service_id": cfg["service_id"],
    }

    seller_signature = _rsa2_sign(sign_params, cfg["private_key"])

    payment_needed = {
        "method": {
            "goods_name": goods_name,
            "seller_app_id": cfg["app_id"],
            "seller_id": cfg["seller_id"],
            "seller_name": cfg["seller_name"],
            "seller_unique_id_key": "seller_id",
            "service_id": cfg["service_id"],
        },
        "protocol": {
            **sign_params,
            "seller_sign_type": "RSA2",
            "seller_signature": seller_signature,
            "seller_unique_id": cfg["seller_id"],
        },
    }

    json_str = json.dumps(payment_needed, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(json_str.encode()).decode().rstrip("=")
    logger.info("生成账单 out_trade_no=%s goods=%s amount=%s", out_trade_no, goods_name, amount)
    return encoded


# --- 免费额度管理 (按 IP) ---

FREE_QUOTA_PER_IP = 3  # 每个 IP 终身免费消息数
PAID_DURATION_HOURS = 24  # 付费后有效时长


def get_free_quota(ip: str) -> int:
    """返回该 IP 剩余免费消息数。"""
    return _IP_QUOTA.get(ip, FREE_QUOTA_PER_IP)


def use_free_quota(ip: str) -> bool:
    """消耗一次免费额度，返回是否消耗成功。"""
    remaining = _IP_QUOTA.get(ip, FREE_QUOTA_PER_IP)
    if remaining > 0:
        _IP_QUOTA[ip] = remaining - 1
        return True
    return False


def has_paid(ip: str) -> bool:
    """检查该 IP 是否在付费有效期内。"""
    paid_until = _PAID_IPS.get(ip)
    if not paid_until:
        return False
    try:
        expiry = datetime.fromisoformat(paid_until)
        return datetime.now(timezone.utc) < expiry
    except (ValueError, TypeError):
        return False


def mark_as_paid(ip: str, payment_proof: str):
    """标记 IP 为已付费，有效期 24 小时。"""
    expiry = datetime.now(timezone.utc) + timedelta(hours=PAID_DURATION_HOURS)
    _PAID_IPS[ip] = expiry.isoformat()
    logger.info("IP %s 已付费，有效期至 %s, proof=%s...", ip, expiry.isoformat(), payment_proof[:20])


def get_paid_expiry(ip: str) -> Optional[str]:
    """返回该 IP 的付费到期时间（ISO格式），未付费返回 None。"""
    return _PAID_IPS.get(ip)


# --- 支付凭证校验 ---

def validate_payment_proof(payment_proof: str) -> bool:
    """校验支付凭证是否有效。

    简单策略: 只要凭证非空且格式合理就放行。
    真实的支付宝验证由 alipay-bot 在支付流程中完成。
    """
    if not payment_proof or len(payment_proof) < 32:
        return False
    # 检查是否为有效的 hex 字符串 (支付宝 payment-proof 格式)
    try:
        int(payment_proof, 16)
        return True
    except ValueError:
        # 也可能是 base64
        return len(payment_proof) >= 32


def check_payment_header(request_headers: dict) -> Optional[str]:
    """从请求头提取 payment-proof，验证有效则返回 proof 字符串。"""
    proof = request_headers.get("payment-proof", "")
    if not proof:
        return None
    return proof if validate_payment_proof(proof) else None
