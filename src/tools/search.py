"""Web 搜索工具 — 已废弃

搜索功能已迁移至 DeepSeek 原生联网搜索（API 层 search: True）。
此文件保留仅为避免导入错误，不再被使用。

请使用 agent.py 中的 chat_completion() 替代。
"""

import json
import logging

logger = logging.getLogger(__name__)


def web_search_impl(query: str, max_results: int = 5, province: str = "") -> str:
    """已废弃。搜索由 DeepSeek 原生联网搜索处理。"""
    logger.warning("web_search_impl 被调用但已废弃——搜索已迁移至 DeepSeek 原生联网搜索")
    return json.dumps({
        "status": "error",
        "message": "搜索功能已迁移至 DeepSeek 原生联网搜索，不再需要单独调用。",
        "query": query, "results": [],
    }, ensure_ascii=False)
