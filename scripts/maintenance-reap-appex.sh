#!/usr/bin/env bash
# ==============================================================================
# Safe WeChatAppEx Renderer & Temp File Maintenance Script
# NEVER kills /usr/bin/wechat main process (preserves WeChat login session 24/7)
# Cron example: 0 20 1,15 * * /usr/local/bin/restart-wechat-bot.sh >/dev/null 2>&1
# ==============================================================================

LOGFILE="/var/log/wechat-maintenance.log"
BEFORE_FREE=$(free -m | awk '/Mem:/ {print $7}')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting safe WeChatAppEx maintenance (avail=${BEFORE_FREE}MB)..." >> "$LOGFILE"

# 1. Terminate orphaned/stale WeChatAppEx Chromium renderer processes running > 10 minutes
REAPED=0
while read -r pid etimes args; do
    if [ -n "$pid" ] && [ "${etimes:-0}" -ge 600 ]; then
        kill -15 "$pid" 2>/dev/null && REAPED=$((REAPED + 1)) || true
    fi
done < <(ps -eo pid=,etimes=,args= | grep "WeChatAppEx" | grep -v "grep" || true)

sleep 2

# Force-reap any stuck WeChatAppEx > 30 minutes that ignored SIGTERM
while read -r pid etimes args; do
    if [ -n "$pid" ] && [ "${etimes:-0}" -ge 1800 ]; then
        kill -9 "$pid" 2>/dev/null || true
    fi
done < <(ps -eo pid=,etimes=,args= | grep "WeChatAppEx" | grep -v "grep" || true)

# 2. Clean stale temporary images in /tmp (>24h) and old cached incoming slices (>14d)
find /tmp -maxdepth 1 -type f \( -name "wechat_ai_gen_*" -o -name "wechat_wp_*" -o -name "wechat_search_*" -o -name "wechat_dl_raw*" \) -mtime +1 -delete 2>/dev/null || true
find /home/opc/wechat_bot/data/images -maxdepth 1 -type f -name "img_*.png" -mtime +14 -delete 2>/dev/null || true

AFTER_FREE=$(free -m | awk '/Mem:/ {print $7}')
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Maintenance done: reaped ${REAPED} WeChatAppEx procs. Avail RAM: ${BEFORE_FREE}MB -> ${AFTER_FREE}MB." >> "$LOGFILE"
