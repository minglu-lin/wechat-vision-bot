#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utilities Plugin: Decision Making, Coin Flip, Dice Rolling
Provides deterministic random choices and fun group tools with instant zero-token replies.
"""

import random
import re
from .base import BasePlugin


class UtilitiesPlugin(BasePlugin):
    name = "decision_and_roll"
    description = "随机决策与掷骰子小工具：支持在群友给出的多个候选项中随机做出决策（如纠结吃什么/选哪个）、掷骰子（点数 1~100）或抛硬币"
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["choose", "dice", "coin"],
                "description": "执行的动作类型：'choose'(从候选项选择), 'dice'(掷骰子), 'coin'(抛硬币)",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "供选择的候选项列表，当 action 为 'choose' 时提供",
            },
            "dice_max": {
                "type": "integer",
                "description": "骰子最大点数，默认为 100",
                "default": 100,
            },
        },
        "required": ["action"],
    }

    fast_patterns = [
        r'^(?:帮我选一个?|帮我决定|选一个|纠结)\s*(?P<options>.+)$',
        r'^(?:掷骰子|摇骰子|丢骰子|扔骰子)(?:\s*(?P<dice_max>\d+))?$',
        r'^(?:抛硬币|丢硬币|掷硬币)$',
    ]

    def match_fast_path(self, text: str):
        t = (text or "").strip()
        # 1. Coin flip
        if re.search(r'^(?:抛硬币|丢硬币|掷硬币)$', t):
            return {"action": "coin"}

        # 2. Dice
        m_dice = re.search(r'^(?:掷骰子|摇骰子|丢骰子|扔骰子)(?:\s*(?P<dice_max>\d+))?$', t)
        if m_dice:
            d_max = int(m_dice.group("dice_max") or 100)
            return {"action": "dice", "dice_max": d_max}

        # 3. Decision
        m_choose = re.search(r'^(?:帮我选一个?|帮我决定|选一个|纠结)\s*[:：]?\s*(?P<options>.+)$', t)
        if m_choose:
            raw_opts = m_choose.group("options")
            # Split by spaces, commas, or "还是"
            parts = re.split(r'[,，\s/、|]+|还是', raw_opts)
            cleaned = [p.strip() for p in parts if p.strip()]
            if len(cleaned) >= 2:
                return {"action": "choose", "options": cleaned}

        return None

    def execute(self, action: str = "choose", options: list = None, dice_max: int = 100, **kwargs) -> dict:
        if action == "coin":
            res = random.choice(["🪙 正面", "🪙 反面"])
            return {"action": "coin", "result": res}
        elif action == "dice":
            d_max = max(2, min(10000, int(dice_max or 100)))
            val = random.randint(1, d_max)
            return {"action": "dice", "dice_max": d_max, "result": val}
        else:
            opts = options or []
            if isinstance(opts, str):
                opts = [p.strip() for p in re.split(r'[,，\s/、|]+|还是', opts) if p.strip()]
            if not opts:
                return {"error": "未提供供选择的候选项"}
            chosen = random.choice(opts)
            return {"action": "choose", "options": opts, "chosen": chosen}

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return "没看清候选项，你列几个我帮选。"

        act = result.get("action")
        if act == "coin":
            side = result['result'].replace("🪙 ", "").strip()
            return f"抛了下硬币，是{side}。"
        elif act == "dice":
            return f"掷了下骰子，{result['result']} 点。"
        else:
            chosen = result.get("chosen", "")
            replies = [
                f"那就选 {chosen}。",
                f"纠结的话听我的，选 {chosen}。",
                f"选 {chosen} 吧。",
                f"我看 {chosen} 行。",
            ]
            return random.choice(replies)
