#!/bin/bash
# serveo 隧道健康检查脚本
# 用法: 由 systemd timer 定期触发

HEALTH_URL="${1:-http://localhost:8000/health}"
SERVEO_PID_FILE="/tmp/serveo_tunnel.pid"
LOG_FILE="/var/log/serveo-tunnel-watchdog.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Check service health
if curl -sf --max-time 5 "$HEALTH_URL" > /dev/null 2>&1; then
    log "OK: 服务健康检查通过 ($HEALTH_URL)"
else
    log "FAIL: 服务健康检查失败 ($HEALTH_URL)"
    exit 1
fi

# Check tunnel process if PID file exists
if [ -f "$SERVEO_PID_FILE" ]; then
    SPID=$(cat "$SERVEO_PID_FILE")
    if kill -0 "$SPID" 2>/dev/null; then
        log "OK: serveo 隧道进程存活 (PID=$SPID)"
    else
        log "FAIL: serveo 隧道进程已死 (PID=$SPID)"
        exit 2
    fi
fi

exit 0
