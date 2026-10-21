#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serper API Key Pool CLI Management Tool
Usage:
    python3 manage_keys.py list
    python3 manage_keys.py add <key1> [<key2> ...]
    python3 manage_keys.py remove <key>
    python3 manage_keys.py test
"""

import sys
import os
import json
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot"))
from plugins.serper_pool import (
    get_serper_pool_status,
    add_serper_keys,
    remove_serper_key,
    discard_exhausted_serper_key,
    get_serper_keys,
)


def cmd_list():
    status = get_serper_pool_status()
    print("=" * 65)
    print(f"🔍 Serper API Key 后备池状态 (共 {status['total_active']} 个可用 Key)")
    print("=" * 65)
    if not status["active_keys"]:
        print("❌ 当前无可用 Key！")
    for item in status["active_keys"]:
        pri_str = "【当前主力 (优先调用)】" if item["is_primary"] else f"【后备 #{item['index']}】"
        print(f"  [{item['index']+1}] {item['key']} {pri_str}")
    if status.get("exhausted_keys"):
        print("\n" + "-" * 65)
        print("🗑️ 已废弃/用尽的 Key：")
        for exh in status["exhausted_keys"]:
            print(f"  ❌ {exh.get('masked', exh.get('key'))} — 废弃时间: {exh.get('discarded_at')} (原因: {exh.get('reason')})")
    print("=" * 65)


def cmd_add(args):
    if not args:
        print("用法: python3 manage_keys.py add <key1> [<key2> ...]")
        return
    added, all_keys = add_serper_keys(args)
    print(f"✅ 成功添加 {added} 个 Key！当前后备池总数: {len(all_keys)}")
    cmd_list()


def cmd_remove(args):
    if not args:
        print("用法: python3 manage_keys.py remove <key>")
        return
    removed, all_keys = remove_serper_key(args[0])
    if removed:
        print(f"🗑️ 成功移除 Key: {args[0]}")
    else:
        print(f"⚠️ 未找到 Key: {args[0]}")
    print(f"当前后备池总数: {len(all_keys)}")


def cmd_test():
    keys = get_serper_keys()
    print("=" * 65)
    print(f"🧪 正在测试后备池中全部 {len(keys)} 个 Key 的连通性与额度...")
    print("=" * 65)
    for idx, k in enumerate(keys):
        masked = f"{k[:6]}...{k[-4:]}" if len(k) >= 10 else k
        try:
            req = urllib.request.Request(
                "https://google.serper.dev/search",
                data=json.dumps({"q": "test", "num": 1}).encode("utf-8"),
                headers={"X-API-KEY": k, "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8", errors="ignore"))
            if data.get("statusCode") in (401, 403, 429) or "credit" in str(data.get("message", "")).lower():
                print(f"❌ Key #{idx+1} [{masked}]: 额度耗尽或失效 ({data.get('message')}) -> 自动废弃")
                discard_exhausted_serper_key(k, reason=str(data.get("message")))
            else:
                organic = data.get("organic", [])
                print(f"✅ Key #{idx+1} [{masked}]: 正常在线 (返回 {len(organic)} 条搜索结果)")
        except urllib.error.HTTPError as he:
            if he.code in (401, 403, 429):
                print(f"❌ Key #{idx+1} [{masked}]: HTTP {he.code} {he.reason} -> 自动废弃")
                discard_exhausted_serper_key(k, reason=f"HTTP {he.code}: {he.reason}")
            else:
                print(f"⚠️ Key #{idx+1} [{masked}]: HTTP 错误 {he.code}")
        except Exception as e:
            print(f"⚠️ Key #{idx+1} [{masked}]: 请求异常 {e}")
    print("=" * 65)
    print("测试完成！最新 Key 池状态：")
    cmd_list()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("list", "-l", "status"):
        cmd_list()
    elif args[0] == "add":
        cmd_add(args[1:])
    elif args[0] in ("remove", "rm", "del"):
        cmd_remove(args[1:])
    elif args[0] in ("test", "check"):
        cmd_test()
    else:
        print("未知子命令。可用命令: list, add <key>, remove <key>, test")
