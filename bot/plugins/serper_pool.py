#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serper.dev Google Search API Key Pool Manager
Manages multiple backup Serper API keys, automatic failover upon credit exhaustion (HTTP 403),
and persistence to config.json.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone, timedelta

TZ_BJ = timezone(timedelta(hours=8))
_lock = threading.Lock()


def get_config_path() -> str:
    candidates = [
        os.environ.get("CONFIG_PATH", ""),
        "/home/opc/wechat_bot/config.json",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json"),
        os.path.join(os.getcwd(), "config.json"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "/home/opc/wechat_bot/config.json"


def get_serper_keys() -> list:
    """获取当前所有可用的 Serper API Keys (按优先级排列)"""
    keys = []
    # 1. 优先读取环境变量
    env_keys = os.environ.get("SERPER_API_KEYS", "").strip()
    if env_keys:
        keys.extend([k.strip() for k in re.split(r'[\r\n,;\s]+', env_keys) if len(k.strip()) >= 20])
    single_env = os.environ.get("SERPER_API_KEY", "").strip()
    if single_env and len(single_env) >= 20 and single_env not in keys:
        keys.append(single_env)

    # 2. 读取 config.json
    cfg_path = get_config_path()
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            list_keys = cfg.get("serper_api_keys", [])
            if isinstance(list_keys, list):
                for k in list_keys:
                    k_str = str(k).strip()
                    if len(k_str) >= 20 and k_str not in keys:
                        keys.append(k_str)
            single_cfg = str(cfg.get("serper_api_key", "")).strip()
            if len(single_cfg) >= 20 and single_cfg not in keys:
                keys.append(single_cfg)
        except Exception as e:
            print(f"[SerperPool] 读取配置文件异常: {e}")

    return keys


def add_serper_keys(new_keys_input) -> tuple:
    """
    添加一个或多个新的 Serper API Key 到后备池。
    new_keys_input 可以是单个 key 字符串、逗号/换行分隔的字符串、或列表。
    返回 (新增数量, 当前所有可用 keys 列表)
    """
    candidates = []
    if isinstance(new_keys_input, str):
        tokens = re.split(r'[\r\n,;\s]+', new_keys_input.strip())
        candidates = [t.strip() for t in tokens if len(t.strip()) >= 20]
    elif isinstance(new_keys_input, (list, tuple)):
        for item in new_keys_input:
            s = str(item).strip()
            if len(s) >= 20:
                candidates.append(s)

    if not candidates:
        return 0, get_serper_keys()

    cfg_path = get_config_path()
    added_count = 0
    with _lock:
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        current_keys = list(cfg.get("serper_api_keys", []))
        if not current_keys and cfg.get("serper_api_key"):
            k_orig = str(cfg["serper_api_key"]).strip()
            if len(k_orig) >= 20:
                current_keys = [k_orig]

        for k in candidates:
            if k not in current_keys:
                current_keys.append(k)
                added_count += 1

        cfg["serper_api_keys"] = current_keys
        if current_keys:
            cfg["serper_api_key"] = current_keys[0]

        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[SerperPool] 保存配置文件异常: {e}")

    return added_count, current_keys


def remove_serper_key(target_key: str) -> tuple:
    """手动从池中移除指定的 Key"""
    t_clean = (target_key or "").strip()
    if not t_clean:
        return False, get_serper_keys()

    cfg_path = get_config_path()
    removed = False
    with _lock:
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        current_keys = list(cfg.get("serper_api_keys", []))
        if not current_keys and cfg.get("serper_api_key"):
            k_orig = str(cfg["serper_api_key"]).strip()
            if len(k_orig) >= 20:
                current_keys = [k_orig]

        if t_clean in current_keys:
            current_keys.remove(t_clean)
            removed = True

        cfg["serper_api_keys"] = current_keys
        cfg["serper_api_key"] = current_keys[0] if current_keys else ""

        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[SerperPool] 移除 Key 异常: {e}")

    return removed, current_keys


def discard_exhausted_serper_key(exhausted_key: str, reason: str = ""):
    """
    当调用 Serper API 返回 403 (Credits exhausted) 或鉴权失效时调用：
    - 自动从可用池中废弃并移除该 Key
    - 将该 Key 归档至 exhausted_serper_keys
    - 持久化至 config.json，确保后续请求不再使用该失效 Key
    """
    t_clean = (exhausted_key or "").strip()
    if not t_clean:
        return

    cfg_path = get_config_path()
    with _lock:
        cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        current_keys = list(cfg.get("serper_api_keys", []))
        if not current_keys and cfg.get("serper_api_key"):
            k_orig = str(cfg["serper_api_key"]).strip()
            if len(k_orig) >= 20:
                current_keys = [k_orig]

        if t_clean in current_keys:
            current_keys.remove(t_clean)

        cfg["serper_api_keys"] = current_keys
        cfg["serper_api_key"] = current_keys[0] if current_keys else ""

        exhausted_list = list(cfg.get("exhausted_serper_keys", []))
        now_str = datetime.now(TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")
        masked = f"{t_clean[:6]}...{t_clean[-4:]}"
        # 避免重复记录相同废弃 key
        if not any(e.get("key") == t_clean for e in exhausted_list):
            exhausted_list.append({
                "key": t_clean,
                "masked": masked,
                "discarded_at": now_str,
                "reason": reason or "Credits exhausted (403)"
            })
        cfg["exhausted_serper_keys"] = exhausted_list[-20:]  # 保留最近20条废弃记录

        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print(f"[SerperPool] ⚠️ Key [{masked}] 额度已耗尽/失效 ({reason})，已自动废弃！剩余后备 Key 数量: {len(current_keys)}")
        except Exception as e:
            print(f"[SerperPool] 保存废弃 Key 异常: {e}")


def get_serper_pool_status() -> dict:
    """返回 Key 池完整状态 (供 Web 界面和 API 读取)"""
    cfg_path = get_config_path()
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    current_keys = list(cfg.get("serper_api_keys", []))
    if not current_keys and cfg.get("serper_api_key"):
        k_orig = str(cfg["serper_api_key"]).strip()
        if len(k_orig) >= 20:
            current_keys = [k_orig]

    active_items = []
    for i, k in enumerate(current_keys):
        masked = f"{k[:6]}...{k[-4:]}" if len(k) >= 10 else k
        active_items.append({
            "index": i,
            "key": k,
            "masked": masked,
            "is_primary": (i == 0),
        })

    exhausted_items = cfg.get("exhausted_serper_keys", [])
    return {
        "ok": True,
        "total_active": len(active_items),
        "active_keys": active_items,
        "exhausted_keys": exhausted_items,
    }
