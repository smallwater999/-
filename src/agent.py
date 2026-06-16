"""高考志愿智能规划师 - LLM 直调模块

彻底放弃 LangGraph + create_agent，直接调用 LLM API。
保留记忆/报告/图表作为可选工具（由 main.py 在需要时调用）。

核心逻辑：
- 纯 LLM 对话，联网搜索通过 web_search 工具实现
- 会话历史由主服务管理（简单列表），不再依赖 LangGraph checkpoint
- 工具调用（报告/图表）由主服务在 LLM 请求外单独处理
"""

import json
import os
import sys
import logging
from typing import List, Dict, Any, Optional

_src_dir = os.path.dirname(os.path.abspath(__file__))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

logger = logging.getLogger(__name__)

_config_cache = None


def load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "agent_llm_config.json"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        _config_cache = json.load(f)
    return _config_cache


def reload_config():
    global _config_cache
    _config_cache = None
    return load_config()


# ============================================================
# LLM Client
# ============================================================

_client = None


def get_client():
    """获取 LLM 客户端实例（带联网搜索）。"""
    global _client
    if _client is None:
        from openai import OpenAI
        cfg = load_config()
        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        model = os.getenv("LLM_MODEL", cfg["config"].get("model", "deepseek-v4-flash-260425"))
        _client = {
            "client": OpenAI(api_key=api_key, base_url=base_url),
            "model": model,
        }
    return _client


def chat_completion(
    messages: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    stream: bool = False,
) -> Any:
    """
    调用 LLM API。

    Args:
        messages: 对话消息列表 [{"role": "user", "content": "..."}, ...]
        system_prompt: 可选的 system prompt（会插入到 messages 最前面）
        temperature: 温度参数
        max_tokens: 最大输出 token
        stream: 是否流式输出

    Returns:
        stream=False: 返回完整响应字符串
        stream=True: 返回迭代器
    """
    client_info = get_client()
    client = client_info["client"]
    model = client_info["model"]

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    kwargs = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
        "extra_body": {
            "search": True,
            "thinking": {"type": "disabled"},
        },
    }

    try:
        response = client.chat.completions.create(**kwargs)
        return response
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        raise
