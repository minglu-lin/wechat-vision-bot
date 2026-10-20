#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeChat Vision AI Bot - 远程控制与运维命令行工具
支持：
- 查看登录状态 (status)
- 拉取最新二维码与微信画面 (qr / screen)
- 智能读取群聊界面内容 (read)
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
    if method == "GET":
        cmd = ["ssh", SSH_HOST, f"curl -s {REMOTE_API}{path}"]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        try:
            return json.loads(res.stdout)
        except Exception:
            return {"raw_output": res.stdout, "stderr": res.stderr}
    else:
        body_str = json.dumps(payload or {}, ensure_ascii=False)
        py_code = (
            "import sys, urllib.request; "
            "data = sys.stdin.buffer.read(); "
            f"req = urllib.request.Request('{REMOTE_API}{path}', data=data, headers={{'Content-Type': 'application/json'}}); "
            "print(urllib.request.urlopen(req, timeout=30).read().decode('utf-8'))"
        )
        cmd = ["ssh", SSH_HOST, f"python3 -c \"{py_code}\""]
        res = subprocess.run(cmd, input=body_str.encode("utf-8"), capture_output=True, timeout=timeout)
        try:
            return json.loads(res.stdout.decode("utf-8"))
        except Exception:
            return {"raw_output": res.stdout.decode("utf-8", errors="ignore"), "stderr": res.stderr.decode("utf-8", errors="ignore")}


def pull_image(remote_path="/tmp/wechat_win.png", local_path="wechat_latest.png"):
    subprocess.run(["scp", f"{SSH_HOST}:{remote_path}", local_path], check=True)
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
  python wechat_ctl.py read [提示词]       # 视觉大模型直接读取群聊界面文本
  python wechat_ctl.py send <文本内容>     # 手动向当前会话输入并发送消息
  python wechat_ctl.py click <x> <y>       # 模拟向界面 (x, y) 处执行鼠标左键点击
  python wechat_ctl.py name <名字1,名字2>  # 动态修改群内被唤醒的关键词/名字
  python wechat_ctl.py prompt <人设内容>   # 动态更新 System Prompt
  python wechat_ctl.py history [条数]      # 查看 SQLite 本地对话记忆库最近记录
======================================================
提示: 默认连接目标 SSH 主机为 'oracle-sg'。
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
        pull_image("/tmp/wechat_win.png", out)
        print(f"已拉取最新微信窗口画面至: {out}")
    elif action == "screen":
        out = sys.argv[2] if len(sys.argv) > 2 else "wechat_screen.png"
        pull_image("/tmp/screen.png", out)
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
        prompt = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "请读取当前微信窗口显示的群名、左侧列表以及右侧最新几条群聊消息内容。"
        resp = remote_api("/api/read_chat", "POST", {"prompt": prompt}, timeout=45)
        print(resp.get("summary", resp))
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
        resp = remote_api("/api/history")
        print("【本地群聊持久化记忆库 (最近记录)】")
        for h in resp.get("history", []):
            tag = "🤖 [BOT]" if h.get("is_bot") else f"👤 [{h.get('sender')}]"
            print(f"[{h.get('time')}] {tag}: {h.get('content')}")
    else:
        print_help()


if __name__ == "__main__":
    main()
