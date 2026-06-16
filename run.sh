#!/bin/bash
set -e

cd "$(dirname "$0")"

PORT=${PORT:-8000}

echo "========================================="
echo "  高考志愿智能规划师"
echo "========================================="
echo ""
echo "安装依赖..."
pip install -r requirements.txt -q

echo ""
echo "启动服务 (端口: $PORT)..."
python src/main.py -p "$PORT" -m http
