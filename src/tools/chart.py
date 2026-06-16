"""雷达图生成工具 - 基于 Matplotlib (线程安全版)"""

import json
import logging
import os
import uuid
import base64
import threading
from io import BytesIO
from typing import Dict, Any

# === CRITICAL: matplotlib.use('Agg') MUST be called BEFORE any pyplot import ===
# This is the ONLY place it should be set — once at module load, never in a function.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

import numpy as np

logger = logging.getLogger(__name__)

CHARTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "charts")

# Threading lock to protect matplotlib drawing (not thread-safe by default)
_plot_lock = threading.Lock()


def generate_radar_chart_impl(data: Dict[str, Any], chart_type: str = "gardner") -> str:
    """生成雷达图，返回 JSON 包含 base64 图片数据"""
    try:
        os.makedirs(CHARTS_DIR, exist_ok=True)

        font_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "NotoSansSC-Regular.ttf")

        # Protect drawing logic with lock (matplotlib is NOT thread-safe)
        with _plot_lock:
            if chart_type == "gardner":
                img_data = _draw_gardner_radar(data, font_path)
            elif chart_type == "mbti":
                img_data = _draw_mbti_bar(data, font_path)
            else:
                img_data = _draw_gardner_radar(data, font_path)

        # Save image outside of lock (I/O is safe)
        filename = f"{chart_type}_{uuid.uuid4().hex[:8]}.png"
        filepath = os.path.join(CHARTS_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(img_data)

        # Base64 编码
        b64 = base64.b64encode(img_data).decode("utf-8")
        img_url = f"data:image/png;base64,{b64}"

        return json.dumps({
            "status": "success",
            "message": "图表生成成功",
            "image_url": img_url,
            "file_path": filepath,
            "chart_type": chart_type,
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"图表生成失败: {e}")
        return json.dumps({
            "status": "error",
            "message": f"图表生成失败: {str(e)[:200]}"
        }, ensure_ascii=False)


def _get_cjk_font(font_path: str):
    """获取中文字体配置"""
    from matplotlib.font_manager import FontProperties
    if os.path.exists(font_path):
        return FontProperties(fname=font_path)
    return None


def _draw_gardner_radar(data: Dict[str, Any], font_path: str) -> bytes:
    """绘制加德纳多元智能雷达图 (caller must hold _plot_lock)"""
    categories = list(data.keys())
    values = [float(data[k]) for k in categories]
    N = len(categories)

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    values += values[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    ax.fill(angles, values, color='#4A90D9', alpha=0.25)
    ax.plot(angles, values, color='#4A90D9', linewidth=2, marker='o', markersize=6)

    font_prop = _get_cjk_font(font_path)
    if font_prop:
        ax.set_xticklabels(categories, fontproperties=font_prop, fontsize=11)
        ax.set_title("加德纳多元智能雷达图", fontproperties=font_prop, fontsize=16, fontweight='bold', pad=20)
    else:
        ax.set_xticklabels(categories, fontsize=11)
        ax.set_title("Gardner Multiple Intelligences Radar", fontsize=14, pad=20)

    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(['20', '40', '60', '80', '100'], fontsize=8)
    ax.fill(angles, values, color='#4A90D9', alpha=0.2)

    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    return buf.getvalue()


def _draw_mbti_bar(data: Dict[str, Any], font_path: str) -> bytes:
    """绘制 MBTI 性格分布图 (caller must hold _plot_lock)"""
    categories = list(data.keys())
    values = [float(data[k]) for k in categories]

    fig, ax = plt.subplots(figsize=(8, 5))
    colors = ['#4A90D9', '#E8734A', '#50B86C', '#F5A623']
    bars = ax.bar(categories, values, color=colors, width=0.5, edgecolor='white', linewidth=1.2)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                str(int(val)), ha='center', va='bottom', fontsize=12, fontweight='bold')

    font_prop = _get_cjk_font(font_path)
    if font_prop:
        ax.set_title("MBTI性格测评结果", fontproperties=font_prop, fontsize=16, fontweight='bold')
        ax.set_ylabel("得分 (%)", fontproperties=font_prop, fontsize=11)
        ax.set_xticklabels(categories, fontproperties=font_prop, fontsize=12)
    else:
        ax.set_title("MBTI Personality Assessment", fontsize=14)
        ax.set_ylabel("Score (%)", fontsize=11)

    ax.set_ylim(0, 105)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    return buf.getvalue()
