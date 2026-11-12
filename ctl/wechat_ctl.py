#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeChat Vision AI Bot - 远程控制与运维命令行工具
支持：
- 查看登录状态 (status)
- 拉取最新二维码与微信画面 (qr / screen)
- 查看最新群聊上下文摘要 (read)
- 手动向群发送消息 (send)
- 模拟点击界面坐标 (click)
- 动态修改机器人触发名字 (name)
- 动态调整 System Prompt 人设 (prompt)
- 查询本地持久化对话记忆库 (history)
"""

import json
import os
import subprocess
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 可通过环境变量 WECHAT_SSH_HOST 覆盖目标服务器别名或 IP
SSH_HOST = os.environ.get("WECHAT_SSH_HOST", "oracle-sg")
REMOTE_API = os.environ.get("WECHAT_API_URL", "http://127.0.0.1:18890")


def remote_api(path, method="GET", payload=None, timeout=35):
    body_bytes = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else b""
    py_code = (
        "import base64, json, os, sys, urllib.request\n"
        "cfg_p = '/home/opc/wechat_bot/config.json'\n"
        "pwd = os.environ.get('BOT_ADMIN_PASSWORD', '')\n"
        "if not pwd and os.path.exists(cfg_p):\n"
        "    pwd = str(json.load(open(cfg_p, encoding='utf-8')).get('admin_password', ''))\n"
        "hdrs = {'Content-Type': 'application/json'}\n"
        "if pwd:\n"
        "    hdrs['Authorization'] = 'Basic ' + base64.b64encode(('admin:' + pwd).encode()).decode()\n"
        f"method = {method!r}\n"
        f"url = {REMOTE_API + path!r}\n"
        "data = sys.stdin.buffer.read() if method != 'GET' else None\n"
        "req = urllib.request.Request(url, data=data, headers=hdrs, method=method)\n"
        "print(urllib.request.urlopen(req, timeout=30).read().decode('utf-8'))\n"
    )
    cmd = ["ssh", SSH_HOST, "python3 -"]
    # Pass script + optional JSON payload separated cleanly via env/arg
    cmd = ["ssh", SSH_HOST, "python3", "-c", py_code]
    res = subprocess.run(cmd, input=body_bytes, capture_output=True, timeout=timeout)
    out_str = res.stdout.decode("utf-8", errors="replace").strip()
    try:
        return json.loads(out_str)
    except Exception:
        return {
            "raw_output": out_str,
            "stderr": res.stderr.decode("utf-8", errors="replace").strip(),
        }


def pull_image(api_endpoint="/api/win.png", local_path="wechat_latest.png"):
    py_code = (
        "import base64, json, os, sys, urllib.request\n"
        "cfg_p = '/home/opc/wechat_bot/config.json'\n"
        "pwd = os.environ.get('BOT_ADMIN_PASSWORD', '')\n"
        "if not pwd and os.path.exists(cfg_p):\n"
        "    pwd = str(json.load(open(cfg_p, encoding='utf-8')).get('admin_password', ''))\n"
        "hdrs = {}\n"
        "if pwd:\n"
        "    hdrs['Authorization'] = 'Basic ' + base64.b64encode(('admin:' + pwd).encode()).decode()\n"
        f"req = urllib.request.Request({REMOTE_API + api_endpoint!r}, headers=hdrs)\n"
        "sys.stdout.buffer.write(urllib.request.urlopen(req, timeout=20).read())\n"
    )
    res = subprocess.run(["ssh", SSH_HOST, "python3", "-c", py_code], capture_output=True, check=True, timeout=30)
    with open(local_path, "wb") as f:
        f.write(res.stdout)
    return local_path


def print_help():
    print("""
======================================================
  WeChat Vision AI Bot - 运维控制工具
======================================================
用法:
  python wechat_ctl.py status              # 查看运行状态与登录详情
  python wechat_ctl.py qr [保存路径]       # 获取最新微信窗口/登录二维码
  python wechat_ctl.py screen [保存路径]   # 获取全屏虚拟桌面截图
  python wechat_ctl.py read [条数]         # 查看当前登录窗口与最近群聊消息摘要
  python wechat_ctl.py send <文本内容>     # 手动向当前会话输入并发送消息
  python wechat_ctl.py click <x> <y>       # 模拟向界面 (x, y) 处执行鼠标左键点击
  python wechat_ctl.py name <名字1,名字2>  # 动态修改群内被唤醒的关键词/名字
  python wechat_ctl.py prompt <人设内容>   # 动态更新 System Prompt
  python wechat_ctl.py history [条数]      # 查看 SQLite 本地对话记忆库最近记录
======================================================
提示: 默认连接目标 SSH 主机为 'oracle-sg'，并在远端自动读取 config.json 完成本地鉴权。
可以通过设置环境变量自定义主机:
  Windows:  $env:WECHAT_SSH_HOST="your-vps-ip"
  Linux/Mac: export WECHAT_SSH_HOST="your-vps-ip"
""")


def main():
    if len(sys.argv) < 2:
        print_help()
        return

    action = sys.argv[1].lower()
    if action == "status":
        st = remote_api("/api/status")
        print(json.dumps(st, ensure_ascii=False, indent=2))
    elif action == "qr":
        out = sys.argv[2] if len(sys.argv) > 2 else "wechat_latest.png"
        pull_image("/api/win.png", out)
        print(f"已拉取最新微信窗口画面至: {out}")
    elif action == "screen":
        out = sys.argv[2] if len(sys.argv) > 2 else "wechat_screen.png"
        pull_image("/api/screen.png", out)
        print(f"已拉取完整屏幕画面至: {out}")
    elif action == "send":
        text = " ".join(sys.argv[2:])
        resp = remote_api("/api/send", "POST", {"text": text})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif action == "click":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        resp = remote_api("/api/click", "POST", {"x": x, "y": y})
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    elif action == "read":
        lim = int(sys.argv[2]) if (len(sys.argv) > 2 and sys.argv[2].isdigit()) else 15
        st = remote_api("/api/status")
        hist = remote_api(f"/api/history?limit={lim}")
        print(f"微信在线状态: {'在线' if st.get('logged_in') else '未登录'} | 关注群聊: {st.get('config', {}).get('focus_chat', '-')}")
        for h in hist.get("history", []):
            tag = "🤖 [BOT]" if h.get("is_bot") else f"👤 [{h.get('sender')}]"
            print(f"[{h.get('time')}] {tag}: {h.get('content')}")
    elif action == "name":
        names = [s.strip() for s in " ".join(sys.argv[2:]).split(",") if s.strip()]
        if "@" not in names:
            names.insert(0, "@")
        resp = remote_api("/api/config", "POST", {"bot_names": names})
        print(f"已更新小号触发词为 {names}: {resp}")
    elif action == "prompt":
        prompt_text = " ".join(sys.argv[2:])
        resp = remote_api("/api/config", "POST", {"system_prompt": prompt_text})
        print(f"已更新 System Prompt: {resp}")
    elif action == "history":
        lim = int(sys.argv[2]) if (len(sys.argv) > 2 and sys.argv[2].isdigit()) else 30
        resp = remote_api(f"/api/history?limit={lim}")
        print(f"【本地群聊持久化记忆库 (最近 {lim} 条记录)】")
        for h in resp.get("history", []):
            tag = "🤖 [BOT]" if h.get("is_bot") else f"👤 [{h.get('sender')}]"
            print(f"[{h.get('time')}] {tag}: {h.get('content')}")
    else:
        print(f"未知命令: {action}")
        print_help()


if __name__ == "__main__":
    main()
