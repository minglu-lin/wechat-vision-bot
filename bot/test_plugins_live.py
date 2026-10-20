#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os

sys.path.insert(0, "/home/opc/wechat_bot")
from bot_daemon import generate_reply_with_search, load_config

cfg = load_config()

print("=== 1. Food Recommendation Test ===")
r_food = generate_reply_with_search("林明露", "bot 北京王府井附近有什么好吃的烤鸭店推荐", cfg)
print("Food Reply:\n", r_food)

print("\n=== 2. Subway Route Test ===")
r_subway = generate_reply_with_search("林明露", "bot 从北京南站坐地铁到颐和园怎么走", cfg)
print("Subway Reply:\n", r_subway)
