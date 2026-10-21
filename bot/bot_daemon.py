#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from PIL import Image, ImageChops, ImageFilter, ImageStat
try:
    from Xlib import X, XK, display
    from Xlib.ext import xtest
except ImportError:
    X = XK = display = xtest = None

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from plugins import plugin_manager, register_image_sender
except Exception as _pie:
    plugin_manager = None
    register_image_sender = None

os.environ["DISPLAY"] = ":99"
BASE_DIR = os.environ.get("BOT_BASE_DIR", "/home/opc/wechat_bot" if os.path.exists("/home/opc/wechat_bot") else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "chat_memory.db")
TZ_BJ = timezone(timedelta(hours=8))

state_lock = threading.Lock()
recent_logs = []
last_send_ts = 0.0
last_reply_text = ""
last_recorded_text = ""
last_bot_reply_ts = 0.0
last_replied_user = ""
import unicodedata

seen_msg_timestamps = {}
seen_content_timestamps = {}
last_bot_db_ts = 0.0


def normalize_dedup_sender(sender):
    s = unicodedata.normalize("NFKC", (sender or "").strip()).lower()
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s+", "", s)
    return s or "群友"


def normalize_dedup_text(content):
    c = unicodedata.normalize("NFKC", (content or "").strip()).lower()
    # Unify @AIbot / @Albot / @aibot / @bot OCR variations (capital I vs lowercase l)
    c = re.sub(r"@\s*(?:aibot|albot|bot|微信小号)\b", "@bot", c)
    c = re.sub(r"^(?:aibot|albot|bot)\b", "@bot", c)
    c = re.sub(r"\s+", "", c)
    return c


def match_and_consume_seen_ts(sender, content, min_ts, consumed_counts):
    """
    在当前屏幕自上而下扫描时，按单调递增时间戳水位线 (min_ts) 与出现次数匹配已处理消息：
    - 若某条消息的历史时间戳早于上方消息/bot回复的时间戳 (t < min_ts - 1.5)，或屏幕上方同名同内容气泡已消耗完历史记录，
      说明屏幕下方这条是用户新发的重复文本（如再次发送 'bot 展开说说'），返回 None（判定为新消息）！
    """
    c_clean = normalize_dedup_text(content)
    s_clean = normalize_dedup_sender(sender)
    if not c_clean:
        return min_ts
    key_full = f"{s_clean}:::{c_clean}"
    ts_list = seen_msg_timestamps.get(key_full, [])
    used_idx = consumed_counts.get(key_full, 0)
    for j in range(used_idx, len(ts_list)):
        t = ts_list[j]
        if t >= min_ts - 1.5:
            consumed_counts[key_full] = j + 1
            return t
    # 兼容同一句话发送者昵称轻微 OCR 抖动（1小时内）
    c_list = seen_content_timestamps.get(c_clean, [])
    c_key = f"__content__:::{c_clean}"
    used_c_idx = consumed_counts.get(c_key, 0)
    now = time.time()
    for j in range(used_c_idx, len(c_list)):
        t = c_list[j]
        if (now - t < 3600) and (t >= min_ts - 1.5):
            consumed_counts[c_key] = j + 1
            return t
    return None


def is_recent_duplicate(sender, content, window=86400, min_ts=0.0):
    now = time.time()
    c_clean = normalize_dedup_text(content)
    s_clean = normalize_dedup_sender(sender)
    if not c_clean:
        return True
    key_full = f"{s_clean}:::{c_clean}"
    ts_list = seen_msg_timestamps.get(key_full, [])
    if ts_list and (now - ts_list[-1] < window) and (ts_list[-1] >= min_ts - 1.5):
        return True
    c_list = seen_content_timestamps.get(c_clean, [])
    if c_list and (now - c_list[-1] < 3600) and (c_list[-1] >= min_ts - 1.5):
        return True
    return False


def mark_seen(sender, content, ts=None, only_if_missing=False):
    ts_val = float(ts) if ts is not None else time.time()
    c_clean = normalize_dedup_text(content)
    s_clean = normalize_dedup_sender(sender)
    if not c_clean:
        return
    key_full = f"{s_clean}:::{c_clean}"
    ts_list = seen_msg_timestamps.setdefault(key_full, [])
    if only_if_missing and ts_list:
        return
    if not ts_list or abs(ts_val - ts_list[-1]) > 1.0:
        ts_list.append(ts_val)
        ts_list.sort()
        if len(ts_list) > 10:
            del ts_list[:-10]
    c_list = seen_content_timestamps.setdefault(c_clean, [])
    if not (only_if_missing and c_list):
        if not c_list or abs(ts_val - c_list[-1]) > 1.0:
            c_list.append(ts_val)
            c_list.sort()
            if len(c_list) > 10:
                del c_list[:-10]


def is_trivial_or_reaction(content):
    if not content:
        return True
    s = content.strip()
    if re.fullmatch(r'(?:\[[a-zA-Z0-9_\u4e00-\u9fa5]+\]|\s)+', s):
        return True
    s_clean = re.sub(r'^[\[\(\"\'\“\‘\s]+|[\]\)\"\'\”\’\s]+$', '', s).strip()
    if not s_clean:
        return True
    if re.fullmatch(r'[\s\?\？\!\！\.\。\…\,\，\-\_\=\+\~\`\^\;\；\:\：\/\、\d]+', s_clean):
        return True
    stripped = re.sub(r'\[[a-zA-Z0-9_\u4e00-\u9fa5]+\]', '', s_clean).strip()
    if not stripped:
        return True
    emoji_pattern = re.compile(
        r'[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50-\u2b55\ufe00-\ufe0f\u200d]+',
        flags=re.UNICODE
    )
    without_emojis = emoji_pattern.sub('', s_clean).strip()
    without_emojis_or_punct = re.sub(r'[\s\?\？\!\！\.\。\…\,\，\-\_\=\+\~\`\^\;\；\:\：\/\、\d]+', '', without_emojis).strip()
    if not without_emojis_or_punct:
        return True
    trivial_words = {
        '好的', '收到', '嗯', '嗯嗯', '哦', '哦哦', 'ok', 'OK', 'Ok', 'oK',
        '好', '行的', '行', '对', '对的', '确实', '赞', '牛逼', '牛', 'nb',
        'tql', '哈哈', '哈哈哈', '呵呵', '原来如此', '懂了', '明白了', '了解',
        '谢了', '谢谢', 'thx', 'thanks', '好嘞', '妥了', '收到收到', '有道理',
        '还行', '不错', '是啊', '就是', '没有', '不是', '不清楚', '不知道', '强',
        '厉害', '666', '6', '？', '?', '！', '!', '...', '。。。', '有意思'
    }
    if without_emojis_or_punct.lower() in trivial_words:
        return True
    return False


def is_stop_command(content):
    if not content:
        return False
    c = content.strip().lower()
    stop_phrases = [
        "闭嘴", "别说了", "别说话", "别接话", "停一下", "停止", "安静", "消停",
        "别吵", "退下", "不用回了", "不用回答", "别回复", "暂停", "打住"
    ]
    for p in stop_phrases:
        if p in c:
            return True
    return False


def is_explicit_bot_invocation(content):
    """
    检查消息是否以 @AIbot 或 bot 开头。
    【用户最高铁律】：@AIbot 或者 bot 开头的句子都必须回应，禁止任何拦截、降级或过滤。
    """
    if not content:
        return False
    c = content.strip().lower()
    if re.fullmatch(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', c):
        return False

    # 1. 以 @ 呼叫 bot: @aibot, @albot, @bot, @微信小号 等，兼容各类 unicode 空格 (\u2005, \u00a0, \u3000)
    if re.match(r'^@\s*(?:aibot|albot|bot|微信小号)', c):
        return True

    # 2. 句首以 bot, aibot, albot 开头 (支持后接空格、标点、全角符号、中文或直接字符)
    if re.match(r'^(?:bot|aibot|albot)(?:[\s\u2005\u00a0\u3000,，:：、?!？！]|$|[\u4e00-\u9fa5])', c):
        return True

    return False


def is_genuine_mention(content, bot_names=None):
    if not content:
        return False
    c = content.strip()
    if re.fullmatch(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', c):
        return False

    c_lower = c.lower()
    candidate_names = ["albot", "aibot", "bot", "小号", "机器人"]
    if bot_names:
        for bn in bot_names:
            bn_clean = bn.strip().lower().lstrip("@")
            if bn_clean and bn_clean not in candidate_names:
                candidate_names.append(bn_clean)

    # 1. 【用户铁律】：bot / @bot 开头直接绝对白名单放行！
    # 只要句子开头是呼叫 bot，绝不进入任何第三人称过滤，直接放行触发回复
    c_no_at = c_lower.lstrip("@").strip()
    for name in candidate_names:
        if c_lower.startswith(name) or c_no_at.startswith(name):
            name_len = len(name)
            rem = c_no_at[name_len:]
            if not rem or rem[0] in [",", "，", ":", "：", " ", "、", "?", "？", "！", "!", "\t"] or ord(rem[0]) > 127:
                return True

    # 2. 显式 @bot 放行（严格校验 @ 后面跟的必须是 bot 相关名称，严禁把 @其他群成员 误判为呼叫 bot）
    if re.search(r'@\s*(?:Albot|albot|bot|Bot|BOT|AIbot|aibot|微信小号|\S*bot|\S*机器人)', c):
        return True
    for name in candidate_names:
        if re.search(r'@\s*' + re.escape(name), c_lower):
            return True

    # 3. 只有非 bot 开头时，才过滤群友闲聊第三人称随口提及（如“不用加bot”、“这个bot好逗”）
    third_person_patterns = [
        r'不用加\s*bot',
        r'不用艾特\s*bot',
        r'不用@',
        r'加\s*bot',
        r'这个\s*bot',
        r'那个\s*bot',
        r'把\s*bot',
        r'看\s*bot',
        r'找\s*bot',
        r'让\s*bot',
        r'问\s*bot',
        r'跟\s*bot',
        r'和\s*bot',
        r'群里的?\s*bot',
        r'给\s*bot',
        r'有\s*bot',
        r'测试\s*bot',
        r'艾特\s*bot',
        r'谁加的\s*bot',
    ]
    for pat in third_person_patterns:
        if re.search(pat, c, re.IGNORECASE):
            return False

    if any(c_lower.endswith(name) for name in candidate_names):
        return True

    return False


def has_followup_intent(content):
    if is_trivial_or_reaction(content):
        return False
    c = content.strip()

    strong_followup_markers = [
        "那你", "那为什么", "那怎么", "那如果", "那要是", "那这个", "那那个", "那他",
        "为什么呢", "为什么会", "为什么啊", "为啥", "凭啥", "怎讲",
        "真的吗", "真的假的", "真假", "后来呢", "然后呢", "接着呢",
        "再查一下", "再查下", "重新查", "再搜一下", "再搜下",
        "详细说说", "详细讲讲", "展开说说", "展开讲讲", "具体说说", "具体讲讲", "具体是指",
        "再解释一下", "再对比一下", "有什么区别", "哪个更好", "怎么理解",
    ]
    for m in strong_followup_markers:
        if m in c:
            return True

    question_keywords = ["为什么", "怎么会", "如何理", "到底是", "具体是", "是否属实"]
    for kw in question_keywords:
        if kw in c:
            return True

    imperative_keywords = ["查一下", "搜一下", "帮我查", "帮我搜", "列出来", "总结一下", "对比一下"]
    for kw in imperative_keywords:
        if kw in c:
            return True

    if c.startswith("那") and (c.endswith("呢") or c.endswith("？") or c.endswith("?")):
        return True

    return False


def is_valid_mp_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("https://mp.weixin.qq.com/s"):
        return True  # 允许普通非微信网页链接
    # 微信公众号短链：/s/<slug>，slug 通常为 15~25 位 hash 字符串，严禁 0/1/null 等占位符
    m1 = re.match(r'^https://mp\.weixin\.qq\.com/s/([a-zA-Z0-9_\-]{8,})', url)
    if m1 and m1.group(1).lower() not in ("0", "1", "null", "undefined", "test"):
        return True
    # 微信公众号全参数长链：/s?__biz=...&mid=...
    if "__biz=" in url and ("mid=" in url or "sn=" in url):
        return True
    return False


def extract_urls(text):
    if not text:
        return []
    urls = re.findall(r'https?://[^\s<>"\')]+', text)
    return [u for u in urls if is_valid_mp_url(u)]


def find_article_link_in_memory(keyword=""):
    try:
        out = subprocess.check_output(["pgrep", "-x", "wechat"]).decode().split()
        if not out:
            return None
        pid = int(out[0])
    except Exception:
        return None

    kw = (keyword or "").strip()
    if not kw:
        return None
    kw_clean = re.sub(r'^[“"\'「\s\U0001f517]+|[”"\'」\s]+$', '', kw)
    if not kw_clean or len(kw_clean) < 2:
        return None
    if len(kw_clean) > 8:
        search_kw = kw_clean[:6]
    else:
        search_kw = kw_clean

    kw_bytes = search_kw.encode("utf-8")

    try:
        with open(f"/proc/{pid}/maps") as f:
            lines = f.readlines()
    except Exception:
        return None

    maps = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and "rw-p" in parts[1] and (len(parts) <= 5 or not parts[5].startswith("/usr")):
            start, end = [int(x, 16) for x in parts[0].split("-")]
            size = end - start
            if 512 * 1024 <= size <= 100 * 1024 * 1024:
                maps.append((start, end))

    maps.reverse()

    try:
        with open(f"/proc/{pid}/mem", "rb", 0) as mem:
            for start, end in maps:
                try:
                    mem.seek(start)
                    chunk = mem.read(end - start)
                    if kw_bytes in chunk:
                        m = re.search(rb'"link"\s*:\s*"(https://mp\.weixin\.qq\.com/s[^"]+)"', chunk)
                        if m:
                            raw_link = m.group(1).decode("utf-8", errors="ignore").replace("&amp;", "&")
                            if is_valid_mp_url(raw_link):
                                return raw_link
                        m2 = re.search(rb'https://mp\.weixin\.qq\.com/s[a-zA-Z0-9_\-\.\/\?\=\&\%]+', chunk)
                        if m2:
                            raw_link = m2.group(0).decode("utf-8", errors="ignore").replace("&amp;", "&").rstrip("\\;&<>'\"")
                            if is_valid_mp_url(raw_link):
                                return raw_link
                except Exception:
                    continue
    except Exception as e:
        log_msg(f"内存扫描链接异常: {e}")

    return None


def fetch_url_content(url, timeout=10, max_chars=3500):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()

            charset = "utf-8"
            if "charset=" in content_type.lower():
                charset = content_type.lower().split("charset=")[-1].split(";")[0].strip()
            try:
                html_str = raw.decode(charset, errors="replace")
            except Exception:
                html_str = raw.decode("utf-8", errors="replace")

        import html as html_lib
        title_m = re.search(r'<meta property="og:title" content="(.*?)"', html_str) or re.search(r'<title[^>]*>(.*?)</title>', html_str, re.IGNORECASE | re.DOTALL)
        title = title_m.group(1).strip() if title_m else ""
        title = re.sub(r'\s+', ' ', title)

        author_m = re.search(r'<meta property="og:article:author" content="(.*?)"', html_str) or re.search(r'var nickname = "(.*?)"', html_str)
        author = author_m.group(1).strip() if author_m else ""

        # WeChat specific article body container
        if "id=\"js_content\"" in html_str:
            content_m = re.search(r'id="js_content"[^>]*>([\s\S]*?)</div>\s*<script', html_str)
            raw_content = content_m.group(1) if content_m else html_str
        else:
            raw_content = html_str

        text = re.sub(r'<!--[\s\S]*?-->', '', raw_content)
        text = re.sub(r'<(script|style|svg|noscript|header|footer|nav)[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?(?:p|div|h[1-6]|li|blockquote|tr|section|article)[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        text = html_lib.unescape(text)

        lines = [line.strip() for line in text.splitlines()]
        cleaned_body = "\n".join([line for line in lines if line])

        if not title and len(cleaned_body) < 120:
            log_msg(f"抓取 URL 失败 ({url}): 无标题且正文过短或无效占位页面")
            return {"success": False, "url": url, "error": "Invalid or placeholder content"}

        if len(cleaned_body) > max_chars:
            cleaned_body = cleaned_body[:max_chars] + "\n...(正文较长，已截取前 3500 字)..."

        log_msg(f"抓取网页链接正文成功 ({url}): 标题='{title}', 作者='{author}', 正文长度={len(cleaned_body)}")
        return {
            "success": True,
            "url": url,
            "title": title,
            "author": author,
            "body": cleaned_body
        }
    except Exception as e:
        log_msg(f"抓取 URL 失败 ({url}): {e}")
        return {"success": False, "url": url, "error": str(e)}


last_processed_ts = 0.0


def init_db():
    global last_processed_ts, last_bot_db_ts
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL,
                time_str TEXT,
                sender TEXT,
                content TEXT,
                is_bot INTEGER,
                is_mentioned INTEGER
            )
        """)
        conn.commit()
        # 启动时按时间由旧到新预热最近 100 条历史消息时间戳列表与最新 bot 回复水位线
        cur.execute("SELECT ts, sender, content, is_bot FROM (SELECT id, ts, sender, content, is_bot FROM messages ORDER BY id DESC LIMIT 100) ORDER BY id ASC")
        rows = cur.fetchall()
        for r_ts, r_sender, r_content, r_is_bot in rows:
            ts_val = float(r_ts or time.time())
            if ts_val > last_processed_ts:
                last_processed_ts = ts_val
            if r_is_bot and ts_val > last_bot_db_ts:
                last_bot_db_ts = ts_val
            raw_c = re.sub(r"^\[(?:分享文章卡片|引用|附图):.*?\]\s*", "", r_content or "").strip()
            for candidate_c in (r_content, raw_c):
                if candidate_c:
                    mark_seen(r_sender, candidate_c, ts=ts_val)
        conn.close()
        log_msg(f"已从数据库预热 {len(rows)} 条历史消息时间戳索引 (当前水位线 ts={last_processed_ts:.2f}, 最新bot回复 ts={last_bot_db_ts:.2f})")
    except Exception as e:
        log_msg(f"Init DB error: {e}")


def save_msg(sender, content, is_bot=False, is_mentioned=False, msg_ts=None):
    global last_bot_db_ts
    content = (content or "").strip()
    if not content:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        ts = float(msg_ts) if msg_ts is not None else time.time()
        if is_bot and ts > last_bot_db_ts:
            last_bot_db_ts = ts
        now_str = datetime.now(TZ_BJ).strftime("%H:%M:%S")
        cur.execute(
            "INSERT INTO messages (ts, time_str, sender, content, is_bot, is_mentioned) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, now_str, sender, content, 1 if is_bot else 0, 1 if is_mentioned else 0)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log_msg(f"Save msg error: {e}")


def get_recent(limit=15):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT time_str, sender, content, is_bot FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        conn.close()
        rows.reverse()
        return [{"time": r[0], "sender": r[1], "content": r[2], "is_bot": bool(r[3])} for r in rows]
    except Exception as e:
        log_msg(f"Get recent history error: {e}")
        return []


def log_msg(msg):
    ts = datetime.now(TZ_BJ).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with state_lock:
        recent_logs.append(line)
        if len(recent_logs) > 80:
            recent_logs.pop(0)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_msg(f"Config load error: {e}")
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def ensure_processes():
    try:
        out = subprocess.check_output(["pgrep", "-f", "Xvfb :99"]).decode().strip()
    except subprocess.CalledProcessError:
        out = ""
    if not out:
        log_msg("Starting Xvfb :99...")
        subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x800x24", "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=open("/tmp/xvfb.log", "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        time.sleep(1.5)

    try:
        out = subprocess.check_output(["pgrep", "-f", "/opt/wechat/wechat|/usr/bin/wechat"]).decode().strip()
    except subprocess.CalledProcessError:
        out = ""
    if not out:
        log_msg("Starting WeChat 4.1...")
        env = os.environ.copy()
        env["DISPLAY"] = ":99"
        env["LANG"] = "zh_CN.UTF-8"
        env["LC_ALL"] = "zh_CN.UTF-8"
        subprocess.Popen(
            ["dbus-run-session", "/usr/bin/wechat"],
            env=env,
            stdout=open("/tmp/wechat.log", "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        time.sleep(4.0)


def find_wechat_windows(d):
    root = d.screen().root
    wins = []

    def walk(w):
        try:
            attrs = w.get_attributes()
            if attrs.map_state == X.IsViewable:
                cls = w.get_wm_class()
                geom = w.get_geometry()
                if cls:
                    cls_lowers = [c.lower() for c in cls]
                    # 自动关闭 WeChatAppEx 网页/小程序弹窗，防止遮挡主聊天窗口
                    if any("wechatappex" in c for c in cls_lowers) and geom.width > 200:
                        try:
                            name = w.get_wm_name() or ""
                            log_msg(f"自动关闭微信网页/小程序弹窗: {name} (id={hex(w.id)})")
                            w.destroy()
                            d.sync()
                            return
                        except Exception:
                            pass

                    if any("wechat" in c for c in cls_lowers):
                        name = w.get_wm_name() or ""

                    if geom.width >= 150 and geom.height >= 150:
                        coords = root.translate_coords(w, 0, 0)
                        wins.append({
                            "win": w,
                            "id": hex(w.id),
                            "name": name,
                            "width": geom.width,
                            "height": geom.height,
                            "x": coords.x,
                            "y": coords.y,
                        })
            for c in w.query_tree().children:
                walk(c)
        except Exception:
            pass

    walk(root)
    # Prioritize official main WeChat window (name == 'WeChat' or '微信') first, then larger subwindows
    def _win_rank(item):
        n = (item["name"] or "").strip().lower()
        is_main = 1 if (n == "wechat" or n == "微信") else 0
        return (is_main, item["width"] * item["height"])
    wins.sort(key=_win_rank, reverse=True)
    return wins


def capture_screen_pil():
    d = display.Display(":99")
    try:
        root = d.screen().root
        geom = root.get_geometry()
        raw = root.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
        img = Image.frombytes("RGB", (geom.width, geom.height), raw.data, "raw", "BGRX")
        wins = find_wechat_windows(d)
        return img, wins
    finally:
        d.close()


def x11_click(x, y):
    d = display.Display(":99")
    try:
        xtest.fake_input(d, X.MotionNotify, x=int(x), y=int(y))
        d.sync()
        time.sleep(0.05)
        xtest.fake_input(d, X.ButtonPress, 1)
        d.sync()
        time.sleep(0.05)
        xtest.fake_input(d, X.ButtonRelease, 1)
        d.sync()
    finally:
        d.close()


def check_and_dismiss_logout_modal(img=None):
    """
    检测并自动解除微信退出登录模态弹窗或残留的全屏大图查看器：
    1. 通过 X11 窗口检测退出登录确认弹窗 (180 < width < 450, 100 < height < 300)；
       若检测到，根据弹窗实际几何坐标精准点击【取消】按钮 (cx, cy) 予以关闭。
       【核心铁律】：绝不发送 Escape 键！在 WeChat 4.x Linux 中，按 Escape 会触发退出登录或重新打开确认弹窗。
    2. 检测是否存在残留全屏图片查看器 (width > 1000, height > 600, 非主窗口)，
       点击右上角关闭按钮将其安全关闭。
    """
    try:
        d = display.Display(":99")
        try:
            wins = find_wechat_windows(d)
            modal_win = None
            viewer_win = None
            for w in wins:
                if 180 < w["width"] < 450 and 100 < w["height"] < 300:
                    modal_win = w
                    break
                elif w["width"] > 1000 and w["height"] > 600 and (w["name"] or "").strip().lower() not in ("wechat", "微信"):
                    viewer_win = w
                    break

            if modal_win:
                cx = modal_win["x"] + int(modal_win["width"] * 0.71)
                cy = modal_win["y"] + int(modal_win["height"] * 0.75)
                log_msg(f"⚠️ 自动检测到微信退出登录确认模态弹窗 (id={modal_win['id']}, pos={modal_win['x']},{modal_win['y']}, size={modal_win['width']}x{modal_win['height']})，正在点击【取消】({cx}, {cy})...")
                xtest.fake_input(d, X.MotionNotify, x=cx, y=cy)
                d.sync()
                time.sleep(0.08)
                xtest.fake_input(d, X.ButtonPress, 1)
                d.sync()
                time.sleep(0.12)
                xtest.fake_input(d, X.ButtonRelease, 1)
                d.sync()
                time.sleep(0.25)

                remaining = [w for w in find_wechat_windows(d) if 180 < w["width"] < 450 and 100 < w["height"] < 300]
                if not remaining:
                    log_msg("✅ 微信退出登录模态弹窗已点击【取消】顺利关闭！")
                else:
                    log_msg(f"⚠️ 点击取消后弹窗仍存在: {remaining[0]['id']}")
                return True

            if viewer_win:
                close_x = viewer_win["x"] + viewer_win["width"] - 25
                close_y = viewer_win["y"] + 20
                log_msg(f"⚠️ 自动检测到微信大图查看器残留窗口 (id={viewer_win['id']})，正在点击右上角关闭 ({close_x}, {close_y})...")
                xtest.fake_input(d, X.MotionNotify, x=close_x, y=close_y)
                d.sync()
                time.sleep(0.08)
                xtest.fake_input(d, X.ButtonPress, 1)
                d.sync()
                time.sleep(0.12)
                xtest.fake_input(d, X.ButtonRelease, 1)
                d.sync()
                time.sleep(0.2)
                return True
        finally:
            d.close()
    except Exception as e:
        log_msg(f"模态检测异常: {e}")
    return False


def recover_wechat_ui_state():
    """
    微信 UI 排错与防卡顿自愈机制：
    1. 检查并关闭微信退出登录确认模态弹窗（点击取消，严禁发送 Escape）
    2. 关闭所有残留的非主窗口（如 WeChatAppEx 网页、弹窗、图片浏览）
    3. 点击主聊天窗口输入框以重新获取焦点
    4. 清空输入框内残留的异常或半截输入 (Ctrl+A -> Backspace)
    """
    try:
        check_and_dismiss_logout_modal()
        d = display.Display(":99")
        try:
            root = d.screen().root
            # 1. 查找并销毁所有可能遮挡的 WeChatAppEx / 辅助窗口
            for c in root.query_tree().children:
                try:
                    cls = c.get_wm_class()
                    if cls:
                        cls_str = " ".join(cls).lower()
                        if "wechatappex" in cls_str:
                            log_msg(f"排错自愈: 关闭残留 WeChatAppEx 窗口 {hex(c.id)}")
                            c.destroy()
                            d.sync()
                except Exception:
                    pass

            # 2. 聚焦输入框并清理输入内容（无需发送 Escape，防止触发全局快捷键）
            wins = find_wechat_windows(d)
            if wins:
                main_w = wins[0]
                click_x = main_w["x"] + int(main_w["width"] * 0.65)
                click_y = main_w["y"] + main_w["height"] - 75
                xtest.fake_input(d, X.MotionNotify, x=click_x, y=click_y)
                xtest.fake_input(d, X.ButtonPress, 1)
                xtest.fake_input(d, X.ButtonRelease, 1)
                d.sync()
                time.sleep(0.05)

                ctrl_kc = d.keysym_to_keycode(XK.XK_Control_L)
                a_kc = d.keysym_to_keycode(XK.XK_a)
                bs_kc = d.keysym_to_keycode(XK.XK_BackSpace)
                xtest.fake_input(d, X.KeyPress, ctrl_kc)
                xtest.fake_input(d, X.KeyPress, a_kc)
                xtest.fake_input(d, X.KeyRelease, a_kc)
                xtest.fake_input(d, X.KeyRelease, ctrl_kc)
                d.sync()
                time.sleep(0.03)
                xtest.fake_input(d, X.KeyPress, bs_kc)
                xtest.fake_input(d, X.KeyRelease, bs_kc)
                d.sync()
        finally:
            d.close()
        log_msg("已完成微信 UI 状态排错与自愈重置")
    except Exception as e:
        log_msg(f"微信 UI 自愈重置异常: {e}")


def x11_send_text(text):
    global last_send_ts, last_reply_text
    check_and_dismiss_logout_modal()
    text = clean_bot_reply(text).strip()
    if not text:
        return False, "Empty text"

    _, wins = capture_screen_pil()
    if not wins:
        return False, "WeChat window not found"
    main_w = wins[0]
    if main_w["width"] < 500:
        return False, "WeChat not logged in yet"

    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    try:
        p = subprocess.Popen(
            ["/usr/local/bin/xclip", "-selection", "clipboard", "-i"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        p.communicate(input=text.encode("utf-8"), timeout=2)
    except Exception as ce:
        log_msg(f"xclip 加载文本到剪贴板异常: {ce}")

    click_x = main_w["x"] + int(main_w["width"] * 0.65)
    click_y = main_w["y"] + main_w["height"] - 75
    x11_click(click_x, click_y)
    time.sleep(0.18)

    d = display.Display(":99")
    try:
        ctrl_kc = d.keysym_to_keycode(XK.XK_Control_L)
        a_kc = d.keysym_to_keycode(XK.XK_a)
        bs_kc = d.keysym_to_keycode(XK.XK_BackSpace)
        v_kc = d.keysym_to_keycode(XK.XK_v)
        ret_kc = d.keysym_to_keycode(XK.XK_Return)

        # 清理输入框可能残留的历史输入
        xtest.fake_input(d, X.KeyPress, ctrl_kc)
        xtest.fake_input(d, X.KeyPress, a_kc)
        xtest.fake_input(d, X.KeyRelease, a_kc)
        xtest.fake_input(d, X.KeyRelease, ctrl_kc)
        d.sync()
        time.sleep(0.05)
        xtest.fake_input(d, X.KeyPress, bs_kc)
        xtest.fake_input(d, X.KeyRelease, bs_kc)
        d.sync()
        time.sleep(0.08)

        # 粘贴并回车发送
        xtest.fake_input(d, X.KeyPress, ctrl_kc)
        xtest.fake_input(d, X.KeyPress, v_kc)
        xtest.fake_input(d, X.KeyRelease, v_kc)
        xtest.fake_input(d, X.KeyRelease, ctrl_kc)
        d.sync()
        time.sleep(0.25)

        xtest.fake_input(d, X.KeyPress, ret_kc)
        xtest.fake_input(d, X.KeyRelease, ret_kc)
        d.sync()
    finally:
        for ks in [XK.XK_Control_L, XK.XK_Control_R, XK.XK_Alt_L, XK.XK_Shift_L]:
            try:
                kc = d.keysym_to_keycode(ks)
                xtest.fake_input(d, X.KeyRelease, kc)
            except Exception:
                pass
        d.sync()
        d.close()

    last_send_ts = time.time()
    last_reply_text = text
    log_msg(f"已发送群聊回复: {text}")
    return True, "OK"


def x11_send_image(image_path_or_url):
    """Send an image to WeChat chat via X11 clipboard."""
    global last_send_ts
    if not image_path_or_url:
        return False, "Empty image path"

    local_path = image_path_or_url
    if image_path_or_url.startswith("http://") or image_path_or_url.startswith("https://"):
        try:
            req = urllib.request.Request(image_path_or_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            local_raw = "/tmp/wechat_dl_raw"
            local_path = "/tmp/wechat_dl.png"
            with open(local_raw, "wb") as f:
                f.write(data)
            Image.open(local_raw).convert("RGB").save(local_path, "PNG")
        except Exception as e:
            log_msg(f"下载待发送图片失败: {e}")
            return False, str(e)

    if not os.path.exists(local_path):
        log_msg(f"待发送图片文件不存在: {local_path}")
        return False, "File not found"

    _, wins = capture_screen_pil()
    if not wins:
        log_msg("发送图片失败: 未找到微信窗口")
        return False, "WeChat window not found"
    main_w = wins[0]
    if main_w["width"] < 500:
        log_msg("发送图片失败: 微信未登录")
        return False, "WeChat not logged in yet"

    env = os.environ.copy()
    env["DISPLAY"] = ":99"
    xclip_bin = "/usr/local/bin/xclip" if os.path.exists("/usr/local/bin/xclip") else "xclip"
    try:
        subprocess.Popen(
            [xclip_bin, "-selection", "clipboard", "-t", "image/png", "-i", local_path],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)
    except Exception as e:
        log_msg(f"xclip 加载图片到剪贴板失败: {e}")
        return False, str(e)

    click_x = main_w["x"] + int(main_w["width"] * 0.65)
    click_y = main_w["y"] + main_w["height"] - 75
    x11_click(click_x, click_y)
    time.sleep(0.2)

    d = display.Display(":99")
    try:
        ctrl_kc = d.keysym_to_keycode(XK.XK_Control_L)
        v_kc = d.keysym_to_keycode(XK.XK_v)
        ret_kc = d.keysym_to_keycode(XK.XK_Return)

        xtest.fake_input(d, X.KeyPress, ctrl_kc)
        xtest.fake_input(d, X.KeyPress, v_kc)
        xtest.fake_input(d, X.KeyRelease, v_kc)
        xtest.fake_input(d, X.KeyRelease, ctrl_kc)
        d.sync()
        time.sleep(0.6)

        xtest.fake_input(d, X.KeyPress, ret_kc)
        xtest.fake_input(d, X.KeyRelease, ret_kc)
        d.sync()
        time.sleep(0.4)
    finally:
        d.close()

    last_send_ts = time.time()
    log_msg(f"已向微信发送图片: {local_path}")
    return True, "OK"


# Register image sender hook for plugins
if register_image_sender:
    register_image_sender(x11_send_image)


def extract_image_via_clipboard(px, py):
    """
    通过右键点击图片气泡 -> 触发菜单第一项‘复制’ -> 从 X11 剪贴板提取原始图片。
    【用户明确要求】：直接右键第一个选项就是复制，严禁双击展开大图！无需点开再去关闭。
    """
    if not display or not xtest:
        return False
    d = None
    try:
        d = display.Display(":99")

        # 1. 鼠标移动到图片气泡中心，右键点击弹出菜单
        xtest.fake_input(d, X.MotionNotify, x=int(px), y=int(py))
        d.sync()
        time.sleep(0.08)
        xtest.fake_input(d, X.ButtonPress, 3)
        xtest.fake_input(d, X.ButtonRelease, 3)
        d.sync()
        time.sleep(0.18)

        # 2. 点击右键菜单中的第一项“复制”（位于右键点偏右下约 35px, 16px）
        xtest.fake_input(d, X.MotionNotify, x=int(px + 35), y=int(py + 16))
        d.sync()
        time.sleep(0.06)
        xtest.fake_input(d, X.ButtonPress, 1)
        xtest.fake_input(d, X.ButtonRelease, 1)
        d.sync()
        time.sleep(0.2)

        # 3. 检查剪贴板是否成功提取到图片数据
        env = os.environ.copy()
        env["DISPLAY"] = ":99"
        xclip_bin = "/usr/local/bin/xclip" if os.path.exists("/usr/local/bin/xclip") else "xclip"

        img_bytes = None
        for target_fmt in ["image/png", "image/jpeg", "image/bmp", "image/x-MS-bmp"]:
            res = subprocess.run(
                [xclip_bin, "-selection", "clipboard", "-t", target_fmt, "-o"],
                env=env,
                capture_output=True,
                timeout=2,
            )
            if len(res.stdout) > 2000:
                img_bytes = res.stdout
                break

        # 如果直接读取为文件路径/URI
        if not img_bytes:
            res_uri = subprocess.run(
                [xclip_bin, "-selection", "clipboard", "-o"],
                env=env,
                capture_output=True,
                timeout=2,
            )
            uri_text = res_uri.stdout.decode("utf-8", errors="ignore").strip()
            if uri_text.startswith("file://"):
                local_file = uri_text.replace("file://", "").strip()
                if os.path.exists(local_file):
                    try:
                        with open(local_file, "rb") as lf:
                            img_bytes = lf.read()
                    except Exception:
                        pass
            elif os.path.exists(uri_text) and (uri_text.endswith(".png") or uri_text.endswith(".jpg") or uri_text.endswith(".dat")):
                try:
                    with open(uri_text, "rb") as lf:
                        img_bytes = lf.read()
                except Exception:
                    pass

        # 4. 点击“复制”已由系统自动收回右键菜单，严禁补发 Escape 键（防止触发微信退出登录）

        if img_bytes and len(img_bytes) > 2000:
            with open("/tmp/wechat_last_photo.png", "wb") as f:
                f.write(img_bytes)
            log_msg(f"成功通过右键菜单直接复制提取图片 ({len(img_bytes)} 字节) -> /tmp/wechat_last_photo.png")
            return True
        else:
            log_msg("右键未获取到剪贴板图片，保留聊天窗口高保真切图兜底")
            return False
    except Exception as e:
        log_msg(f"右键复制图片异常: {e}")
        return False
    finally:
        if d:
            try:
                d.close()
            except Exception:
                pass


def call_routed_llm(provider: str, model_name: str, messages: list, tools: list = None, timeout: int = 65, temperature: float = None, reasoning_effort: str = None):
    """
    通用多模型分流调用引擎，统一调度 Kimi 与 Gemini 两大服务商，原生支持 temperature 与 reasoning_effort。
    """
    cfg = load_config()
    if provider == "kimi":
        api_key = cfg.get("kimi_api_key") or cfg.get("api_key", "")
        base_url = cfg.get("kimi_base_url", "https://api.kimi.com/coding/v1/chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "KimiCLI/0.77",
        }
    else:  # gemini
        api_key = cfg.get("gemini_api_key") or cfg.get("api_key", "")
        base_url = cfg.get("gemini_base_url") or cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    payload = {
        "model": model_name,
        "messages": messages,
    }

    if provider == "kimi":
        # Note: Kimi Coding API (kimi-for-coding-highspeed) strictly only supports temperature=1,
        # passing other values returns HTTP 400: 'invalid temperature: only 1 is allowed for this model'.
        # Disable internal thinking/CoT to prevent raw English/Chinese reasoning from leaking into content and cut latency by 50%.
        payload["thinking"] = {"type": "disabled"}
    else:
        target_temp = temperature if temperature is not None else cfg.get("temperature", 0.75)
        try:
            payload["temperature"] = float(target_temp)
        except (ValueError, TypeError):
            payload["temperature"] = 0.75
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort

    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class TaskModelRouter:
    """
    智能多模型分流路由引擎
    用户配额特征：Kimi 额度充裕，Gemini 额度珍贵。
    1. Nano Banana Pro (gemini-3-pro-image): 绘画/AI生图/垫图重制改图 (由 generate_ai_image 插件独占)
    2. Kimi (kimi-for-coding-highspeed): 主力文本对话、长文/文章卡片深度阅读总结、日常问答、生活工具查询 (承担 70%+ 主力日常流量，保护 Gemini 额度)
    3. Gemini 4 Argon (gemini-4-argon): 深度推理、复杂编程、算法设计、数理推导与形式化证明 (高难度任务专精)
    4. Gemini 3.8 Flash (gemini-3.8-flash): X11 屏幕后台视觉检测、群聊附图/截图等多模态读图、Kimi/Argon 的快速容灾降级
    """

    DEEP_REASONING_PATTERNS = [
        r'(?:写一段?|编写|实现|手写|设计)(?:个|一个)?(?:代码|程序|脚本|算法|函数|类|模块)',
        r'(?:代码|程序|算法)(?:重构|优化|调试|排查|分析|实现)',
        r'(?:leetcode|动态规划|二叉树|红黑树|快速排序|递归|贪心|图论)',
        r'(?:数学证明|公式推导|求导|微积分|拉格朗日|矩阵|线性代数|特征值|傅里叶|偏微分)',
        r'(?:深度思考|深度分析|底层原理|架构设计|形式化验证|博弈论)',
        r'(?:gemini\s*4|argon|4-argon)',
    ]

    MULTIMODAL_IMAGE_PATTERNS = [
        r'(?:看下?|看一看|识别|分析|提取)(?:这张?|这个?|上面这个?|附件)?(?:图|照片|图片|截图)',
        r'(?:图里|照片里|截图里)(?:写了什么|有什么|是啥|报错|文字)',
        r'(?:gemini\s*3\.?8|flash)',
    ]

    @classmethod
    def route(cls, content: str, quote_info: dict = None, image_info: dict = None) -> dict:
        c = (content or "").strip().lower()
        has_img = bool(image_info and image_info.get("has_image"))

        # 1. 多模态/附图解析诉求 -> 分流给 Gemini 3.8 Flash (Kimi 纯文本 API 无法接收图片数据)
        if has_img or any(re.search(p, c) for p in cls.MULTIMODAL_IMAGE_PATTERNS):
            log_msg(f"【模型分流路由】多模态视觉图文理解 -> 分流至 Gemini 3.8 Flash")
            return {
                "provider": "gemini",
                "model": "gemini-3.8-flash",
                "reason": "多模态视觉图文解析",
            }

        # 2. 复杂编程/算法/数学证明/深度推理 -> 分流给 Gemini 4 Argon
        if any(re.search(p, c) for p in cls.DEEP_REASONING_PATTERNS):
            log_msg(f"【模型分流路由】高难度编程/数理推导/深度逻辑推理 -> 分流至 Gemini 4 Argon")
            return {
                "provider": "gemini",
                "model": "gemini-4-argon",
                "reason": "深度代码与数理逻辑推理",
            }

        # 3. 主力日常对话、微信文章卡片阅读、长文本阅读、日常查询、生活工具调用 -> 分流给 Kimi
        # （充分消耗充裕的 Kimi 额度，最大限度节约宝贵的 Gemini 额度）
        log_msg(f"【模型分流路由】常规群聊文本问答/长文阅读/工具调用 -> 分流至 Kimi (充裕额度主力)")
        return {
            "provider": "kimi",
            "model": "kimi-for-coding-highspeed",
            "reason": "主力日常问答与长文阅读 (Kimi 充裕额度)",
        }


def call_llm_chat(messages, tools=None, timeout=75, override_model=None):
    model = override_model or "gemini-3.8-flash"
    provider = "kimi" if "kimi" in model.lower() else "gemini"
    return call_routed_llm(provider, model, messages, tools=tools, timeout=timeout)


call_kimi_chat = call_llm_chat


def parse_recent_messages_with_vision(chat_img, bot_names, last_replied_user="", session_active=False, chat_box=None):
    try:
        chat_img.save("/tmp/wechat_last_chat_img.png")
    except Exception:
        pass
    buf = io.BytesIO()
    chat_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    session_instruction = ""
    if session_active and last_replied_user:
        session_instruction = (
            f"【30秒连续追问判断准则（非常重要，严禁滥答）】：bot 刚在 30 秒内回答了群友【{last_replied_user}】。\n"
            f"- 只有当【{last_replied_user}】发送的新消息是【明确针对 bot 上一轮回答的有实质提问内容的追问或明确操作指令】时，才将 is_followup 设为 true！例如：“那你再查一下...”、“为什么会这样呢”、“真的吗”、“后来怎么样了”、“详细讲讲”。\n"
            f"- 【下列情况必须设 is_followup 为 false，绝不可设为 true】：\n"
            f"  1. 单纯表情符号或表情包（如 👍、😂、[强]、[表情] 等）\n"
            f"  2. 单纯标点符号（如 ?、？、!、... 等）\n"
            f"  3. 敷衍附和、感谢或确认（如：好的、收到、明白、对、嗯、ok、谢谢、确实）\n"
            f"  4. 闲聊感叹或向其他人发表评论（如：“原来是这样啊”、“不用加bot也会回”、“哈哈好玩”、“太强了”）\n"
            f"  5. 没有明确提问意图或指令诉求的普通陈述句。\n\n"
        )

    prompt = (
        f"这是一张微信群聊窗口最新截图。\n"
        f"请自上而下（按时间由旧到新顺序）提取聊天记录区域中可见的最新的 1~6 条消息列表（包括群友发的白色气泡、我们自己发的绿色气泡以及图片消息）：\n\n"
        f"针对每条消息，提取以下字段：\n"
        f"1. sender: 发送者群昵称（若是右侧绿色气泡，则为我们自己/bot）\n"
        f"2. content: 消息主体文本内容（若是图片，简要描述图片类型与核心画面）\n"
        f"3. is_incoming: 是否是群友发送的左侧白色气泡（true 为群友发送，false 为右侧绿色气泡）\n"
        f"4. is_mentioned: 该消息是否在【显式呼叫/指令召唤】bot？\n"
        f"   - 【用户最高铁律】：只要消息开头包含 @AIbot、@Albot、@bot、@微信小号，或者以 bot、aibot 开头（如 'bot ...'、'@AIbot ...'），无论后面是什么内容，is_mentioned 必须强制为 true！\n"
        f"   - 【严格禁忌1】：如果群友是在 @其他群成员（例如 '@张三 ...'、'@黄嘉诚 ...'，@后面跟的不是 AIbot/Albot/bot），绝对不属于呼叫 bot，is_mentioned 必须为 false！\n"
        f"   - 【严格禁忌2】：当群友在第三人称随口谈论提到 bot（例如“不用加bot你也会回”、“这个bot好逗”、“加bot干嘛”），是在与群友闲聊而非指令时，is_mentioned 必须为 false！\n"
        f"5. is_followup: 是否是对上一轮回答的连续追问（严格按照下方连续追问准则判断）\n"
        f"6. quote: 是否使用了微信‘引用’（has_quote, quoted_sender, quoted_content）\n"
        f"7. referenced_image: 是否包含图片或关联图片（has_image, image_type, ocr_and_detail: 详细提取图片可见文字或核心画面, box_2d: 归一化坐标 [ymin, xmin, ymax, xmax]，取值0到1000之间）\n"
        f"8. article_card: 该消息是否是微信图文分享卡片（如公众号文章卡片、新闻卡片、第三方链接卡片）？\n"
        f"   - 若是卡片：is_card=true, title(卡片完整标题), source(卡片底部来源/媒体名，如大河报、澎湃新闻、知乎等), url(若有可见链接则填写，无则留空)\n"
        f"   - 若不是卡片：is_card=false\n\n"
        f"{session_instruction}"
        f"请严格输出纯 JSON，不要带 markdown 代码块（不要带 ```json）：\n"
        f'{{\n'
        f'  "messages": [\n'
        f'    {{\n'
        f'      "sender": "昵称",\n'
        f'      "content": "消息正文",\n'
        f'      "is_incoming": true,\n'
        f'      "is_mentioned": true,\n'
        f'      "is_followup": false,\n'
        f'      "quote": {{"has_quote": false, "quoted_sender": "", "quoted_content": ""}},\n'
        f'      "referenced_image": {{"has_image": false, "image_type": "", "ocr_and_detail": "", "box_2d": []}},\n'
        f'      "article_card": {{"is_card": false, "title": "", "source": "", "url": ""}}\n'
        f'    }}\n'
        f'  ]\n'
        f'}}\n'
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }
    ]
    res = None
    last_v_err = None
    vision_models = [
        ("gemini-3.1-flash-lite", None),
        ("gemini-3.8-flash", "low"),
    ]
    for v_attempt, (v_model, v_effort) in enumerate(vision_models):
        try:
            res = call_routed_llm(
                "gemini",
                v_model,
                messages,
                timeout=15,
                temperature=0.1,
                reasoning_effort=v_effort,
            )
            break
        except Exception as ve:
            last_v_err = ve
            log_msg(f"视觉读图请求异常 [{v_model}] (第 {v_attempt+1}/2 次): {ve}")
            if v_attempt == 0:
                time.sleep(0.4)
    if res is None:
        raise last_v_err

    text = res["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        if "{" in text and "}" in text:
            json_str = text[text.find("{"):text.rfind("}")+1]
            data = json.loads(json_str)
        else:
            data = json.loads(text)

        if isinstance(data, dict):
            msgs = data.get("messages", [])
            for m in msgs:
                ref_img = m.get("referenced_image")
                if ref_img and ref_img.get("has_image") and ref_img.get("box_2d"):
                    box = ref_img["box_2d"]
                    if isinstance(box, list) and len(box) == 4:
                        try:
                            ymin, xmin, ymax, xmax = box
                            cw, ch = chat_img.size
                            crop_box = (
                                max(0, int(xmin * cw / 1000)),
                                max(0, int(ymin * ch / 1000)),
                                min(cw, int(xmax * cw / 1000)),
                                min(ch, int(ymax * ch / 1000))
                            )
                            if crop_box[2] - crop_box[0] > 20 and crop_box[3] - crop_box[1] > 20:
                                ref_img["_crop_box"] = list(crop_box)
                                if chat_box:
                                    screen_cx = chat_box[0] + int(((xmin + xmax) / 2) * (chat_box[2] - chat_box[0]) / 1000)
                                    screen_cy = chat_box[1] + int(((ymin + ymax) / 2) * (chat_box[3] - chat_box[1]) / 1000)
                                    ref_img["_screen_pos"] = [screen_cx, screen_cy]
                        except Exception as ce:
                            log_msg(f"计算图片坐标失败: {ce}")
            if isinstance(msgs, list):
                return msgs
            elif "content" in data:
                return [data]
        elif isinstance(data, list):
            return data
        return []
    except Exception as je:
        log_msg(f"Vision JSON 解析兜底: {je}")
        is_incoming = "true" in text.lower() and '"is_incoming": true' in text.lower()
        is_mentioned = "true" in text.lower() and '"is_mentioned": true' in text.lower()
        return [{
            "is_incoming": is_incoming,
            "sender": "群友",
            "content": text,
            "is_mentioned": is_mentioned,
            "is_followup": False,
            "quote": {"has_quote": False, "quoted_sender": "", "quoted_content": ""},
            "referenced_image": {"has_image": False, "image_type": "", "ocr_and_detail": ""},
        }]


def clean_bot_reply(text):
    if not text:
        return ""

    # 0. Strip <think>...</think> blocks or leaked internal CoT / pseudo-tool monologues (e.g. "公办 func... Execute search. Use tools... ）")
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE)
    if any(k in text for k in ["Execute search", "Use tools", "Let's search", "Let's call", "tool_calls", "web_search("]):
        # If leaked CoT ends with ） or ) followed by Chinese text, strip the leaked prefix
        m_cot = re.search(r'(?:Execute search|Use tools|Let\'s search|Let\'s call)[\s\S]*?[）\)]\s*(?=[\u4e00-\u9fa5])', text)
        if m_cot:
            text = text[m_cot.end():].strip()

    # 1. Strip raw tool search directives and JSON query blocks
    text = re.sub(r'\$web_search(?::\d+)?\s*\{[^}]*\}', '', text)
    text = re.sub(r'```(?:json)?\s*\{\s*"query"[\s\S]*?\}\s*```', '', text)

    # 2. LaTeX math blocks and inline math delimiters
    # Display formulas: $$ formula $$ or \[ formula \]
    text = re.sub(r'\$\$([\s\S]*?)\$\$', r'\1', text)
    text = re.sub(r'\\\[([\s\S]*?)\\\]', r'\1', text)
    # Inline formulas: $ formula $ or \( formula \)
    text = re.sub(r'\$([^\$\n]+?)\$', r'\1', text)
    text = re.sub(r'\\\(([\s\S]*?)\\\)', r'\1', text)

    # 3. Common LaTeX command transformations to clean plain text
    # Fractions: \frac{a}{b} -> a/b
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'\1/\2', text)
    # Square roots: \sqrt{a} -> √(a)
    text = re.sub(r'\\sqrt\{([^{}]+)\}', r'√(\1)', text)
    # Text styles in formulas: \text{...}, \mathrm{...}, \mathbf{...}, \boldsymbol{...}
    text = re.sub(r'\\(?:text|mathrm|mathbf|boldsymbol|operatorname|textbf|textit)\{([^{}]+)\}', r'\1', text)
    # Exponents and subscripts: x^{2} -> x^2, x_{1} -> x_1
    text = re.sub(r'\^\{([^{}]+)\}', r'^\1', text)
    text = re.sub(r'_\{([^{}]+)\}', r'_\1', text)

    # Common math symbols to unicode / readable text
    math_syms = {
        r'\\times\b': '×',
        r'\\div\b': '÷',
        r'\\cdot\b': '·',
        r'\\pm\b': '±',
        r'\\approx\b': '≈',
        r'\\neq\b': '≠',
        r'\\le(q)?\b': '≤',
        r'\\ge(q)?\b': '≥',
        r'\\infty\b': '∞',
        r'\\sum\b': '∑',
        r'\\prod\b': '∏',
        r'\\int\b': '∫',
        r'\\alpha\b': 'α',
        r'\\beta\b': 'β',
        r'\\gamma\b': 'γ',
        r'\\delta\b': 'δ',
        r'\\epsilon\b': 'ε',
        r'\\theta\b': 'θ',
        r'\\lambda\b': 'λ',
        r'\\mu\b': 'μ',
        r'\\pi\b': 'π',
        r'\\sigma\b': 'σ',
        r'\\phi\b': 'φ',
        r'\\omega\b': 'ω',
        r'\\Delta\b': 'Δ',
        r'\\to\b': '→',
        r'\\rightarrow\b': '→',
        r'\\leftarrow\b': '←',
        r'\\Rightarrow\b': '⇒',
        r'\\Leftarrow\b': '⇐',
        r'\\degree\b': '°',
        r'\\%': '%',
    }
    for pat, rep in math_syms.items():
        text = re.sub(pat, rep, text)

    # Clean leftover backslashes before common escaped punctuation
    text = re.sub(r'\\([%$&_#~])', r'\1', text)

    # 4. Markdown formatting to plain text
    # Strip code block fences like ```python or ``` but preserve code text
    text = re.sub(r'```[a-zA-Z0-9_\-+]*\n?', '', text)
    # Bold & Italic: **bold**, *italic*, __bold__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*([^\*\n]+?)\*', r'\1', text)
    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # Inline code: `code`
    text = re.sub(r'`([^`\n]+?)`', r'\1', text)
    # Markdown links: [title](url) -> title (url)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'\1 (\2)', text)
    # Headers: # Header -> Header
    text = re.sub(r'^[ \t]*#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Blockquotes: > quote -> quote
    text = re.sub(r'^[ \t]*>\s*', '', text, flags=re.MULTILINE)
    # Bullet lists: - item or * item -> • item
    text = re.sub(r'^[ \t]*[-*]\s+', '• ', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[ \t]*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)

    # 5. Clean empty lines and search residual lines
    lines = [l.strip() for l in text.splitlines()]
    cleaned_lines = []
    for l in lines:
        if not l:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        if l.startswith("$web_search"):
            continue
        cleaned_lines.append(l)

    return "\n".join(cleaned_lines).strip()


def generate_reply_with_search(sender, content, cfg, recent_history=None, quote_info=None, image_info=None, force_provider=None):
    # 0. Fast-Path Direct Execution (0 Token, <100ms ultra-low latency)
    if plugin_manager:
        matched, fast_reply = plugin_manager.match_fast_path(content)
        if matched and fast_reply:
            log_msg(f"命中 Fast-Path 快速直通插件: '{content}' -> 直接返回预置数据")
            return fast_reply

    sys_prompt = cfg.get("system_prompt", "")
    route_info = TaskModelRouter.route(content, quote_info=quote_info, image_info=image_info)
    provider = route_info["provider"]
    target_model = route_info["model"]
    route_reason = route_info["reason"]
    if force_provider == "gemini" and provider == "kimi":
        provider = "gemini"
        target_model = "gemini-3.8-flash"
        route_reason = "重试强制切换 Gemini 3.8 Flash 兜底"

    plugin_tools = plugin_manager.get_openai_tools() if plugin_manager else []
    tools = plugin_tools

    now_str = datetime.now(TZ_BJ).strftime("%Y年%m月%d日 %H:%M")

    history_str = ""
    if recent_history:
        lines = [f"[{h['time']}] {h['sender']}: {h['content']}" for h in recent_history]
        history_str = "【群聊最近上下文记录（供参考理解语境、代词与话题连贯性）】：\n" + "\n".join(lines) + "\n\n"

    # Dynamic scene intent detection for length & style control
    content_lower = content.lower()
    is_deep_eval = any(kw in content_lower for kw in [
        "评价", "分析", "解读", "怎么看", "如何看", "你怎么认为", "深度", "对比", "优缺点", "讲了什么", "研报", "总结", "详细说", "为什么"
    ]) or (quote_info and any(kw in str(quote_info.get("quoted_content", "")).lower() for kw in ["《", "http", "mp.weixin.qq.com"]))

    is_factual_lookup = any(kw in content_lower for kw in [
        "推荐", "哪家", "哪里", "买衣服", "商场", "餐馆", "美食", "攀岩", "酒吧", "健身房", "地铁", "路线", "怎么走", "多少钱", "汇率", "天气", "价格", "报错", "怎么解决", "怎么配", "代码"
    ])

    is_chat_banter = not is_deep_eval and not is_factual_lookup and len(content.strip()) < 30

    if is_chat_banter:
        scene_guidance = (
            "- 当前对话场景：群聊日常闲聊/吐槽/轻松接话。\n"
            "- 回复篇幅：极简，通常1~2句话即可，就像真实群友在微信随手打字。\n"
            "- 表达要点：接住对方的情绪、笑点或最值得接的那句话。严禁长篇大论、严禁列数字清单、严禁在结尾追加反问或套话。"
        )
    elif is_deep_eval:
        scene_guidance = (
            "- 当前对话场景：观点分析/新闻解读/深度提问。\n"
            "- 回复篇幅：根据分析深度自然展开，说透核心逻辑。\n"
            "- 表达要点：先给结论和关键依据，再结合真实事实展开分析，观点客观鲜明，有理有据，避免废话。"
        )
    else:
        scene_guidance = (
            "- 当前对话场景：信息查询/生活推荐/技术问答。\n"
            "- 回复篇幅：简明直接，就事论事。\n"
            "- 表达要点：直接给出推荐结果、核心数据或解决方案与关键理由，不拖泥带水，不用客服客套。"
        )

    system_instruction = (
        f"{sys_prompt}\n"
        f"【当前系统时间】：{now_str}（北京时间）。\n"
        f"{history_str}"
        f"【实时 API 工具调用指引（高优先级）】：\n"
        f"- 系统已接入丰富实时的本地与在线 API 工具库：\n"
        f"  • 查全品类生活地点与商户 (search_local_places)：商场购物、男装/女装买衣服、攀岩馆/健身房/运动场馆、酒吧夜生活、文化场馆/博物馆，返回真实门店名、高德/口碑评分、人均消费预算与详细地址。\n"
        f"    - 【重点指引】：当群友询问买衣服、商场、男装/女装、攀岩馆、健身房、运动场馆、酒吧、文化场馆等北京地点推荐时，必须优先调用 search_local_places 检索真实 POI 数据！若需深入了解大众点评榜单或网友评价，可进一步调用 web_search 联网检索。\n"
        f"  • 查美食推荐 (search_local_food)：城市与商圈特色餐饮、招牌好店、人均预算与口碑\n"
        f"  • 查地铁与公交路线 (get_transit_route)：城市公共交通与地铁换乘方案、几号线、换乘站点、用时与票价\n"
        f"  • 汇率与加密货币 (get_exchange_rate, get_crypto_price)：实时法币汇率与主流数字货币行情\n"
        f"  • 查IP归属地 (lookup_ip)：公网IP与域名物理位置、ISP运营商\n"
        f"  • 科学计算 (calculate)：数学公式、求幂、函数计算\n"
        f"  • 随机决策与掷骰子 (decision_and_roll)：多选项决策、掷骰子、抛硬币\n"
        f"  • AI 绘画与图片重制 (generate_ai_image)：\n"
        f"    - 支持从零文生图（风景、人物、插画、动物、壁纸等）。\n"
        f"    - 特别支持【垫图重制与照片修改（图生图）】：当群友要求'重制一下这个照片'、'改一下这个图'、'把照片里的人变成...'、'仿照这张图'、'换成...'时，必须立即调用 generate_ai_image，并设置 use_ref_image=true！系统已将群聊中的照片作为高精度垫图，能保真重制背景与构图！\n"
        f"  • 全网搜图与表情包 (search_image)：搜索真实高清照片、壁纸、萌宠摄影或指定主题表情包并直接发送到群聊\n"
        f"- 当群友提出绘画、作图、生图、重制照片、改图等诉求时，请优先调用 generate_ai_image；当群友要求找真实照片、看图、要某类表情包或壁纸时，调用 search_image。\n"
        f"- 调用生图或搜图工具后，系统已自动把图片发送到群聊中，你只需自然亲切地告知群友图片已发送，并用一两句生动的话介绍画面内容即可！\n"
        f"【时效性与事实搜索指引】：如果对方询问了需要时效性、实时事实、当天股市/行情、最新新闻、具体人物或资料查询等，请务必使用联网搜索工具获取最新真实数据。\n"
        f"【链接与新闻文章卡片深度阅读指引（核心能力）】：\n"
        f"- 如果提问附带或关联了网页链接（HTTP/HTTPS），系统已自动下载抓取了网页标题与完整正文，请紧密结合提取出的正文内容进行回答；\n"
        f"- 如果群聊中分享了微信文章卡片、新闻卡片、或群友要求对刚刚分享的文章‘评价一下’、‘总结下’、‘分析下’、‘你怎么看’、‘讲了什么’等：你绝对不能只凭标题凭空推测或说‘光从标题看’！必须立即调用 $web_search 联网搜索工具获取该文章的完整正文和真实事实细节，以完整报道事实为基础进行客观、深刻、有理有据的分析评价！\n"
        f"【微信引用与群聊图片处理指引】：\n"
        f"- 如果群友在本次提问中使用了微信‘引用’，请特别针对被引用的历史内容进行针对性答复；\n"
        f"- 如果提问附带或关联了群聊中刚刚发送的图片/截图（如搜索结果截图、代码报错、报表、照片等），系统已通过视觉模型提取了图片中的文字、关键词、搜索列表和画面详情，请务必紧密结合图片中的实际文字与证据进行回答，万不可无视图片细节！\n"
        f"【排版与格式铁律（严禁Markdown/LaTeX）】：微信无法解析渲染 Markdown 或 LaTeX 格式！\n"
        f"- 严禁使用任何星号粗体（如 **文字**）、斜体（*文字*）、反引号代码框（`文字`）或 # 标题符！\n"
        f"- 严禁使用 LaTeX 数学公式符号（如 $...$ 或 $$...$$）；\n"
        f"- 必须输出如同普通人在微信打字一样的纯文本（Plain Text）。列项时可直接使用数字编号（如 1. 2.）或圆点符号（• ），重点内容直接写出，切勿加粗或带语法标记。\n"
        f"【场景与回答规范】：\n"
        f"{scene_guidance}\n"
        f"- 结合最近聊天记录，准确理解群友话语中的代词（如‘这句话’、‘刚才说的’、‘他’、‘那个’等）以及话题脉络，保持连贯记忆。\n"
        f"- 保持好相处、自然、松弛的朋友口吻，分寸感要好，严禁客服腔和AI味。严禁‘很高兴为您解答’‘希望对您有所帮助’‘如有其他问题欢迎随时提问’等任何客服模板套话。\n"
        f"- 直接输出面向群友的最终纯文本回答，严禁输出任何‘我查一下’、‘稍等’等过渡性碎碎念，绝对严禁输出任何代码、JSON、搜索函数名或 `$web_search` 等内部指令标签！\n\n"
        f"【群聊表达示范（仅参考语气、节奏与精简度，严禁机械重复）】：\n"
        f"群友：周一又不想上班。\n"
        f"bot：才周一，电量已经见底了。\n"
        f"群友：你说得太长了。\n"
        f"bot：行，我说短点。\n"
        f"群友：这个方案肯定没问题吧？\n"
        f"bot：我还不敢打包票，主要看那个前提能不能成立。\n"
        f"群友：北京西站附近有啥好吃的？\n"
        f"bot：南广场出来走两步有家老字号炙子烤肉，或者北广场食宝街转转，看你想吃快餐还是踏实坐下吃。\n"
        f"群友：今天怎么这么冷？\n"
        f"bot：冷空气突袭降温贼猛，出门裹紧大衣别冻着。"
    )

    user_query = f"{sender}: {content}"
    if quote_info and quote_info.get("has_quote"):
        q_sender = quote_info.get("quoted_sender", "群友")
        q_content = str(quote_info.get("quoted_content", "")).strip()
        if q_content:
            user_query += f"\n【群友本次引用的历史消息】（被引用人: {q_sender}）：\n{q_content}"

    if image_info and image_info.get("has_image"):
        img_type = image_info.get("image_type", "图片")
        img_ocr = str(image_info.get("ocr_and_detail", "")).strip()
        if img_ocr:
            user_query += f"\n【群聊中关联/引用的图片识别详情（视觉OCR与画面解析）】：\n- 图片类别：{img_type}\n- 图片画面与文字详情：{img_ocr}"

    # 1. Automatic URL extraction & web page content fetching
    is_visual_query = False
    if image_info and image_info.get("has_image"):
        is_visual_query = True
    elif any(k in content for k in ["图", "图片", "这图", "看图", "走势图", "截图", "照片", "k线"]):
        is_visual_query = True

    candidate_texts = [content]
    if quote_info and quote_info.get("has_quote"):
        candidate_texts.append(str(quote_info.get("quoted_content", "")))

    found_urls = []
    for ct in candidate_texts:
        for u in extract_urls(ct):
            if is_valid_mp_url(u) and u not in found_urls:
                found_urls.append(u)

    # 2. If no explicit HTTP link found in text, check for WeChat Article Card or quote with 🔗
    # ONLY scan memory if this is NOT a visual/image query, AND the user explicitly provided or quoted an article card!
    if not found_urls and not is_visual_query:
        card_kw = ""
        for ct in candidate_texts:
            if "🔗" in ct:
                card_kw = re.sub(r'.*?🔗\s*', '', ct).strip()
                break
            m = re.search(r'\[分享文章卡片:\s*《(.*?)》', ct)
            if m:
                card_kw = m.group(1).strip()
                break

        # Only allow explicit article references (e.g. user mentions 文章/链接/公众号)
        if not card_kw and any(k in content for k in ["链接", "文章", "公众号", "卡片", "推文"]):
            for ct in candidate_texts:
                m = re.search(r'《(.*?)》', ct)
                if m and len(m.group(1).strip()) >= 4:
                    card_kw = m.group(1).strip()
                    break

        if card_kw:
            mem_url = find_article_link_in_memory(card_kw)
            if mem_url and is_valid_mp_url(mem_url):
                log_msg(f"微信内存成功解析出真实链接: {mem_url}")
                found_urls.append(mem_url)

    fetched_any_article = False
    for u in found_urls[:2]:
        fetch_res = fetch_url_content(u)
        if fetch_res.get("success"):
            fetched_any_article = True
            user_query += (
                f"\n【系统已成功将该分享卡片/链接解析还原为真实链接并抓取了完整正文】（真实链接: {u}）：\n"
                f"- 文章完整标题：《{fetch_res['title']}》\n"
                f"- 作者/媒体来源：{fetch_res.get('author') or '媒体专栏'}\n"
                f"- 网页报道全文正文内容：\n{fetch_res['body']}\n"
                f"【极其重要的回答铁律】：你现在已经 100% 拿到了该链接/卡片文章的真实报道全文！\n"
                f"当群友询问‘能看到里面的内容了吗’、‘评价一下’或询问文章讲了什么时，你必须明确回答【能看到完整内容】，并紧密围绕上面抓取到的真实正文细节（案情细节、来龙去脉、双方说法、法院裁判逻辑等）进行全面透彻的解答与评价！绝对严禁再说‘看不到完整内容’或‘只带了标题片段’！\n"
            )

    # 3. Fallback: If memory URL search failed, but title is present, trigger $web_search
    if not fetched_any_article and not is_visual_query:
        card_title = ""
        card_source = ""
        for ct in candidate_texts:
            m = re.search(r'\[分享文章卡片:\s*《(.*?)》(?:[ \t]*来源:[ \t]*([^\]]+))?', ct)
            if m:
                card_title = m.group(1).strip()
                card_source = (m.group(2) or "").strip()
                break
            m_book = re.search(r'《(.*?)》', ct)
            if m_book and len(m_book.group(1).strip()) >= 4 and any(k in content for k in ["文章", "新闻", "报道", "链接"]):
                card_title = m_book.group(1).strip()
                break

        if card_title:
            if plugin_manager:
                log_msg(f"【前置联网搜索】检测到分享卡片《{card_title}》，先调用 Serper Google 搜索 API 获取全文报道再交由 AI 整理判断...")
                pre_res = plugin_manager.execute_tool("web_search", {"query": f"{card_title} {card_source}".strip()})
                if isinstance(pre_res, dict) and pre_res.get("count", 0) > 0:
                    user_query += (
                        f"\n\n【系统已预先通过 Google (Serper API) 检索到该分享文章《{card_title}》的权威报道摘要】：\n"
                        f"{json.dumps(pre_res.get('results', []), ensure_ascii=False, indent=2)}\n"
                        f"【核心要求】：请直接结合上述检索到的真实事件细节进行全面、客观、深入的分析评价，无需再次调用搜索工具！\n"
                    )
                    tools = None
            if tools is not None:
                user_query += (
                    f"\n【重要任务：群友正在要求阅读与评价/分析分享的文章】：\n"
                    f"- 分享文章标题：《{card_title}》\n"
                    f"- 媒体/发布来源：{card_source or '权威新闻媒体'}\n"
                    f"- 核心要求：请使用 web_search 检索该文章的真实事件细节后再进行分析评价。\n"
                )
        else:
            # 4. 针对搜索类/事实核查/人物事迹/新闻热点/国内外百科问题（含引用后的“展开说说/细说”）：先调用 Serper 搜索 API，再交给 AI 整理判断输出！
            clean_q = re.sub(r'^(?:@\s*(?:aibot|albot|bot|微信小号)|(?:aibot|albot|bot))[\s,，:：、?!？！]*', '', content.strip(), flags=re.IGNORECASE).strip()
            search_intent_kws = [
                "谁", "什么事", "事迹", "是谁", "什么人", "哪位", "新闻", "最新", "最近", "怎么回事", "出了什么",
                "为什么", "为啥", "哪家", "哪里", "多少钱", "价格", "行情", "汇率", "百科", "历史", "事件",
                "真假", "真的吗", "搜", "查", "名人", "教授", "大学", "博士", "法院", "判决", "公司",
                "发生", "结局", "评测", "推荐", "区别", "哪年", "什么时候", "什么梗", "来历", "出处", "死", "去世"
            ]
            expand_followup_kws = ["展开", "细说", "详细", "具体", "多说", "深入", "再查", "深挖", "更多细节", "详细讲"]
            quoted_text_clean = str((quote_info or {}).get("quoted_content", "")).strip()
            is_expand_with_quote = bool(quoted_text_clean and any(kw in clean_q for kw in expand_followup_kws))
            if plugin_manager and ((len(clean_q) >= 4 and any(kw in clean_q for kw in search_intent_kws)) or is_expand_with_quote):
                search_query_str = clean_q
                if is_expand_with_quote:
                    # Extract key topic from the first clause/sentence of the quoted content so Serper searches the actual subject
                    first_clause = re.split(r'[。！？\n]', quoted_text_clean)[0][:45].strip()
                    search_query_str = f"{first_clause} {clean_q}".strip()
                log_msg(f"【前置联网搜索】命中搜索/事实核查类任务，先调用 Serper Google 搜索 API: '{search_query_str}'")
                pre_res = plugin_manager.execute_tool("web_search", {"query": search_query_str})
                if isinstance(pre_res, dict) and pre_res.get("count", 0) > 0:
                    log_msg(f"【前置联网搜索】Serper 检索成功 (返回 {pre_res.get('count')} 条国内外结果)，交由 AI 整理判断一轮直出！")
                    user_query += (
                        f"\n\n【系统已预先调用 Google (Serper API) 检索到的国内外实时资料（请据此整理、判断并直接输出回答，无需再调工具）】：\n"
                        f"{json.dumps(pre_res.get('results', []), ensure_ascii=False, indent=2)}\n"
                    )
                    tools = None

    user_content_payload = user_query
    if provider == "gemini" and image_info and image_info.get("has_image") and os.path.exists("/tmp/wechat_last_photo.png"):
        try:
            with open("/tmp/wechat_last_photo.png", "rb") as f:
                img_data = f.read()
            if img_data and len(img_data) > 1000:
                img_b64 = base64.b64encode(img_data).decode("ascii")
                user_content_payload = [
                    {"type": "text", "text": user_query},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
                ]
                log_msg(f"已将群聊原图 ({len(img_data)} 字节) 作为视觉多模态输入直接送入 Gemini 3.8 Flash")
        except Exception as e:
            log_msg(f"组装图片多模态 payload 异常: {e}")

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_content_payload},
    ]

    final_reply = ""
    collected_tool_outputs = []
    for loop_idx in range(3):
        try:
            # 严格限制最多只允许第 1 轮 (loop_idx == 0) 触发工具调用；第 2 轮起强制 tools=None 直接根据已有信息+自身知识作答，彻底杜绝无限连环搜！
            active_tools = tools if (loop_idx == 0 and not collected_tool_outputs) else None
            log_msg(f"模型调用 [服务商={provider}, 模型={target_model}, 策略={route_reason}] 第 {loop_idx+1} 轮 (tools={'开' if active_tools else '关-强制总结'})...")
            res = call_routed_llm(provider, target_model, messages, tools=active_tools, timeout=32)
            choice = res["choices"][0]
            msg = choice["message"]

            if active_tools and (choice.get("finish_reason") == "tool_calls" or msg.get("tool_calls")) and msg.get("tool_calls"):
                log_msg(f"模型 ({target_model}) 触发工具调用 (第 {loop_idx+1} 轮): 共 {len(msg['tool_calls'])} 个工具")
                for tc in msg["tool_calls"][:2]:
                    fn_name = tc["function"]["name"]
                    fn_args = tc["function"].get("arguments", "{}")
                    log_msg(f"执行 API 插件: {fn_name}({fn_args})")
                    if plugin_manager:
                        tool_res = plugin_manager.execute_tool(fn_name, fn_args)
                    else:
                        tool_res = {"error": "Plugin manager not loaded"}
                    res_str = json.dumps(tool_res, ensure_ascii=False) if isinstance(tool_res, (dict, list)) else str(tool_res)
                    log_msg(f"API 插件返回结果 ({fn_name}): {res_str[:120]}")
                    collected_tool_outputs.append(f"[{fn_name}({fn_args})]: {res_str}")
                # 重要修复：第 2 轮禁用 tools 时，切勿在 messages 中保留 assistant.tool_calls 与 role='tool'，
                # 否则 Kimi 在缺少 tools 定义时会发生模板解析错乱并把内部英文思考过程 (Execute search. Use tools...) 泄露进正文！
                # 直接将工具返回结果组装为干净的单轮 user 上下文交由第 2 轮直接整理输出：
                synth_query = (
                    user_query
                    + "\n\n【系统已通过实时工具/Google (Serper API) 检索到的参考资料（请直接据此整理、判断并输出最终纯中文回答，严禁输出任何思考过程或工具调用指令）】：\n"
                    + "\n".join(collected_tool_outputs)
                )
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": synth_query},
                ]
                continue

            content_text = (msg.get("content") or "").strip()
            final_reply = clean_bot_reply(content_text)
            if final_reply:
                break
            # 如果 Kimi 返回空内容，下一轮自动切 Gemini 兜底
            if provider == "kimi":
                log_msg("Kimi 返回空内容，自动切换备用 Gemini 3.8 Flash 直接生成回答...")
                provider = "gemini"
                target_model = "gemini-3.8-flash"
                route_reason = "Kimi 空返回容灾降级"
                fallback_query = user_query
                if collected_tool_outputs:
                    fallback_query += "\n\n【系统已为你检索到的实时参考资料】：\n" + "\n".join(collected_tool_outputs)
                tools = None
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": fallback_query},
                ]
                continue
        except Exception as e:
            log_msg(f"模型 [{provider}/{target_model}] 请求异常 (第 {loop_idx+1} 轮): {e}")
            # 容灾自动分流降级：如果 Kimi 超时或出错（含400内容过滤），无缝切换 Gemini 3.8 Flash 兜底
            if provider == "kimi":
                log_msg("Kimi 服务调用异常，重置干净上下文并自动切换备用 Gemini 3.8 Flash 进行快速容灾重试...")
                provider = "gemini"
                target_model = "gemini-3.8-flash"
                route_reason = "Kimi 异常容灾降级"
                fallback_query = user_query
                if collected_tool_outputs:
                    fallback_query += "\n\n【系统已为你检索到的实时参考资料】：\n" + "\n".join(collected_tool_outputs)
                tools = None
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": fallback_query},
                ]
                continue
            # 如果 Gemini 4 Argon 异常，降级至 Gemini 3.8 Flash
            elif target_model == "gemini-4-argon":
                log_msg("Gemini 4 Argon 异常，重置上下文并降级至 Gemini 3.8 Flash 继续重试...")
                target_model = "gemini-3.8-flash"
                route_reason = "Argon 异常容灾降级"
                fallback_query = user_query
                if collected_tool_outputs:
                    fallback_query += "\n\n【系统已为你检索到的实时参考资料】：\n" + "\n".join(collected_tool_outputs)
                tools = None
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": fallback_query},
                ]
                continue
            break

    return final_reply


def get_persona_error_reply(query_text=""):
    import random
    candidates = [
        "刚才网络抖了一下没连上，过会儿我再瞅瞅。",
        "脑子卡了一下刚才没查出来，稍等我再试下。",
        "刚才网络波动没连上，过会儿再试。",
        "网络刚才卡了下没发出来，过会儿再问我试试。",
        "刚才走神了没连上，过两分钟再问我试试。"
    ]
    return random.choice(candidates)


def safe_generate_and_reply(target, cfg, recent_history, prefix_sender=False, pre_send_check_fn=None):
    """
    执行带有自动排错、多级兜底与防丢单重试机制的回复生成与发送。
    确保 @AIbot / bot 消息绝不漏回，支持多用户并发队列 @昵称 区分与思考期新消息检测。
    """
    s = target["sender"]
    c = target["content"]
    q_info = target.get("quote")
    img_info = target.get("image")
    is_explicit = target.get("is_explicit", False)

    max_attempts = 3 if is_explicit else 2
    reply_content = ""
    concurrent_arrival = False

    for attempt in range(1, max_attempts + 1):
        try:
            log_msg(f"尝试生成回复 [第 {attempt}/{max_attempts} 次] (sender={s}, is_explicit={is_explicit}): {c[:30]}")
            reply_content = generate_reply_with_search(
                s,
                c,
                cfg,
                recent_history=recent_history,
                quote_info=q_info,
                image_info=img_info,
                force_provider="gemini" if attempt >= 2 else None,
            )
            if reply_content and reply_content.strip():
                if pre_send_check_fn:
                    try:
                        if pre_send_check_fn():
                            concurrent_arrival = True
                    except Exception:
                        pass
                if (prefix_sender or concurrent_arrival) and s and s not in ("群友", "未知", "bot") and not reply_content.startswith(f"@{s}"):
                    reply_content = f"@{s} {reply_content}"
                ok, send_res = x11_send_text(reply_content)
                if ok:
                    save_msg("bot", reply_content, is_bot=True)
                    mark_seen("bot", reply_content)
                    mark_seen(s, c, only_if_missing=True)
                    return True, reply_content, concurrent_arrival
                else:
                    log_msg(f"x11 发送文本失败: {send_res}，尝试 UI 自愈排错...")
                    recover_wechat_ui_state()
            else:
                log_msg(f"回复内容为空 (第 {attempt} 次)，触发 UI 排错自愈与重试...")
                recover_wechat_ui_state()
                time.sleep(1.0)
        except Exception as err:
            log_msg(f"回复流程异常 (第 {attempt} 次): {err}")
            recover_wechat_ui_state()
            time.sleep(1.0)

    # 必须回应的显式指令 (is_explicit)：若多次重试仍未成功，启动最终兜底回复，严防卡顿沉默
    if is_explicit:
        try:
            log_msg(f"触发必须回应紧急保底回复机制 (sender={s}, query={c[:25]})")
            recover_wechat_ui_state()
            emergency_reply = get_persona_error_reply(c)
            if (prefix_sender or concurrent_arrival) and s and s not in ("群友", "未知", "bot"):
                emergency_reply = f"@{s} {emergency_reply}"
            ok, _ = x11_send_text(emergency_reply)
            if ok:
                save_msg("bot", emergency_reply, is_bot=True)
                mark_seen("bot", emergency_reply)
                mark_seen(s, c, only_if_missing=True)
                return True, emergency_reply, concurrent_arrival
        except Exception as ee:
            log_msg(f"紧急保底回复发送失败: {ee}")

    return False, "", concurrent_arrival


def find_unread_badge_in_sidebar(img, w):
    """Scan the left conversation list for a red unread notification badge (R>215, G<85, B<85)."""
    sx1 = w["x"] + 10
    sy1 = w["y"] + 90
    sx2 = w["x"] + 190
    sy2 = w["y"] + w["height"] - 40
    sub = img.crop((sx1, sy1, sx2, sy2))
    px = sub.load()
    sw, sh = sub.size
    for y in range(0, sh, 2):
        for x in range(0, sw, 2):
            r, g, b = px[x, y]
            if r > 215 and g < 85 and b < 85:
                return w["x"] + 100, sy1 + y + 5
    return None


def monitor_loop():
    prev_crop = Image.new("L", (160, 120), 0)
    anim_mask = None
    force_rescan = False
    reply_task_queue = deque()
    last_check_reply_ts = 0.0
    global last_recorded_text, last_bot_reply_ts, last_replied_user, last_reply_text, last_send_ts
    log_msg("监控循环已启动 (WeChat 4.1 + Kimi Vision + FIFO 任务队列 + Local Memory)")

    def is_in_task_queue(sender, content):
        norm_c = normalize_dedup_text(content)
        norm_s = normalize_dedup_sender(sender)
        for item in reply_task_queue:
            if normalize_dedup_sender(item.get("sender")) == norm_s and normalize_dedup_text(item.get("content")) == norm_c:
                return True
        return False

    while True:
        try:
            ensure_processes()
            cfg = load_config()
            img, wins = capture_screen_pil()
            img.save("/tmp/screen.png")

            if check_and_dismiss_logout_modal(img):
                time.sleep(0.3)
                img, wins = capture_screen_pil()
                img.save("/tmp/screen.png")

            if wins:
                w = wins[0]
                pad = 12
                box = (
                    max(0, w["x"] - pad),
                    max(0, w["y"] - pad),
                    min(img.width, w["x"] + w["width"] + pad),
                    min(img.height, w["y"] + w["height"] + pad),
                )
                img.crop(box).save("/tmp/wechat_win.png")

                if w["width"] >= 550 and cfg.get("auto_reply", True):
                    # Auto-click unread red badge in sidebar if any (only when current queue is empty)
                    if not reply_task_queue:
                        badge_pos = find_unread_badge_in_sidebar(img, w)
                        if badge_pos:
                            log_msg(f"检测到左侧会话列表红点未读消息，自动点击打开: {badge_pos}")
                            x11_click(badge_pos[0], badge_pos[1])
                            time.sleep(0.6)
                            img, wins = capture_screen_pil()
                            anim_mask = None

                    rx1 = w["x"] + int(w["width"] * 0.33)
                    ry1 = w["y"] + int(w["height"] * 0.38)
                    rx2 = w["x"] + int(w["width"] * 0.80)
                    ry2 = w["y"] + w["height"] - 140
                    cur_crop = img.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))

                    # 阶段一 (Producer)：检测画面新动态或思考期插话，扫描并推入 FIFO 任务队列
                    if force_rescan or (time.time() - last_send_ts > 2.2):
                        if prev_crop is not None:
                            diff = ImageChops.difference(cur_crop, prev_crop)
                            if anim_mask is not None and not force_rescan:
                                diff.paste(0, mask=anim_mask)
                            mean_diff = ImageStat.Stat(diff).mean[0]
                            if force_rescan or (mean_diff > 1.2 and (time.time() - last_check_reply_ts > 1.8)):
                                was_force_rescan = force_rescan
                                log_msg(f"检测到聊天窗口新动态 (diff={mean_diff:.2f}, force={force_rescan})，分析最新消息...")
                                force_rescan = False
                                time.sleep(0.25)
                                img2, _ = capture_screen_pil()
                                chat_box = (
                                    w["x"] + int(w["width"] * 0.29),
                                    w["y"] + 8,
                                    w["x"] + w["width"] - 8,
                                    w["y"] + w["height"] - 125,
                                )
                                chat_img = img2.crop(chat_box)
                                new_crop = img2.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                                last_check_reply_ts = time.time()

                                bot_names = [b for b in cfg.get("bot_names", ["AIbot", "Albot", "aibot", "bot"]) if b.strip() != "@"]
                                trigger_mode = cfg.get("trigger_mode", "mention_only")

                                try:
                                    session_active = (time.time() - last_bot_reply_ts < 30) and bool(last_replied_user)
                                    raw_msgs = parse_recent_messages_with_vision(
                                        chat_img, bot_names, last_replied_user=last_replied_user, session_active=session_active, chat_box=chat_box
                                    )

                                    reply_candidates = []
                                    new_msg_count = 0
                                    batch_ts = time.time()
                                    global last_processed_ts

                                    # 1. 时间戳水位线与屏幕底部锚点定位 (cutoff_idx)：
                                    # 微信窗口自上而下严格按时间由旧到新排列 (t_0 <= t_1 <= ... <= t_n)。
                                    # 维护单调递增时间戳下界 running_min_ts 与同屏出现次数消费表 consumed_counts：
                                    # - 若某条消息位于 bot 最新回复下方 (running_min_ts >= last_bot_db_ts)，而它在去重表中的旧记录时间戳 < running_min_ts，
                                    #   或者同屏上方同名同内容气泡已经消耗掉那条旧时间戳记录，则判定屏幕下方这条是用户再次发送的新消息（绝不误杀重复指令如再次发 'bot 展开说说'）！
                                    last_bot_idx = -1
                                    for idx, m_item in enumerate(raw_msgs):
                                        m_in = m_item.get("is_incoming", True)
                                        m_c = (m_item.get("content") or "").strip()
                                        if m_c and not is_explicit_bot_invocation(m_c) and not m_in:
                                            last_bot_idx = idx

                                    cutoff_idx = -1
                                    running_min_ts = 0.0
                                    consumed_counts = {}
                                    for idx, m_item in enumerate(raw_msgs):
                                        m_in = m_item.get("is_incoming", True)
                                        m_s = m_item.get("sender", "群友")
                                        m_c = (m_item.get("content") or "").strip()
                                        if not m_c:
                                            continue
                                        if is_explicit_bot_invocation(m_c):
                                            m_in = True
                                        if not m_in:
                                            matched_bot_t = match_and_consume_seen_ts("bot", m_c, running_min_ts, consumed_counts)
                                            if matched_bot_t is not None:
                                                running_min_ts = max(running_min_ts, matched_bot_t)
                                            if idx == last_bot_idx and last_bot_db_ts > 0:
                                                running_min_ts = max(running_min_ts, last_bot_db_ts)
                                            mark_seen("bot", m_c, ts=running_min_ts or last_processed_ts, only_if_missing=True)
                                            if not was_force_rescan:
                                                cutoff_idx = max(cutoff_idx, idx)
                                        else:
                                            matched_t = match_and_consume_seen_ts(m_s, m_c, running_min_ts, consumed_counts)
                                            if matched_t is not None or is_in_task_queue(m_s, m_c):
                                                if matched_t is not None:
                                                    running_min_ts = max(running_min_ts, matched_t)
                                                cutoff_idx = max(cutoff_idx, idx)

                                    # 将水位线及上方所有旧消息的当前 OCR 文本变体静默补充至去重表（only_if_missing=True 不覆盖原历史时间戳）
                                    for idx in range(0, cutoff_idx + 1):
                                        m_old = raw_msgs[idx]
                                        c_old = (m_old.get("content") or "").strip()
                                        if c_old:
                                            mark_seen(m_old.get("sender", "群友"), c_old, ts=running_min_ts or last_processed_ts, only_if_missing=True)

                                    # 2. 仅对水位线下方 (idx > cutoff_idx) 的增量新消息打上新批次时间戳 batch_ts 并按批次处理
                                    for idx in range(cutoff_idx + 1, len(raw_msgs)):
                                        msg_item = raw_msgs[idx]
                                        is_in = msg_item.get("is_incoming", True)
                                        s = msg_item.get("sender", "群友")
                                        c = msg_item.get("content", "").strip()
                                        is_m = msg_item.get("is_mentioned", False)
                                        is_f = msg_item.get("is_followup", False)
                                        q_info = msg_item.get("quote", {})
                                        img_info = msg_item.get("referenced_image", {})

                                        if not c:
                                            continue

                                        # Deduplicate against messages after running_min_ts & pending queue
                                        if is_recent_duplicate(s, c, min_ts=running_min_ts) or is_in_task_queue(s, c):
                                            continue

                                        new_msg_count += 1

                                        # 检查是否为最高优先级强行召唤指令 (@AIbot 或 bot 开头)
                                        is_explicit = is_explicit_bot_invocation(c)
                                        if is_explicit:
                                            is_in = True
                                            is_m = True

                                        # Format recorded text with card, quote and image tags
                                        card_info = msg_item.get("article_card", {})
                                        has_card = card_info.get("is_card", False) if isinstance(card_info, dict) else False
                                        has_q = q_info.get("has_quote", False) if isinstance(q_info, dict) else False
                                        has_img = img_info.get("has_image", False) if isinstance(img_info, dict) else False
                                        rec_c = c
                                        if has_card and card_info.get("title"):
                                            c_title = card_info.get("title", "").strip()
                                            c_src = card_info.get("source", "").strip()
                                            c_url = card_info.get("url", "").strip()
                                            card_prefix = f"[分享文章卡片: 《{c_title}》" + (f" 来源: {c_src}" if c_src else "") + (f" 链接: {c_url}" if c_url else "") + "]"
                                            rec_c = card_prefix + (" " + rec_c if rec_c and rec_c != c_title else "")
                                        if has_q and q_info.get("quoted_content"):
                                            rec_c = f"[引用 {q_info.get('quoted_sender')}: {str(q_info.get('quoted_content'))[:35]}] " + rec_c
                                        if has_img and img_info.get("image_type"):
                                            rec_c = f"[附图: {img_info.get('image_type')}] " + rec_c

                                        # 写入当前批次时间戳 batch_ts 并立即注册去重时间戳，防止重复纳入
                                        save_msg(s, rec_c, is_bot=not is_in, is_mentioned=is_m, msg_ts=batch_ts)
                                        mark_seen(s, c, ts=batch_ts)
                                        last_processed_ts = batch_ts

                                        if not is_in:
                                            log_msg(f"提取自身发送消息: {c[:25]}，已记入本地历史")
                                            continue

                                        # 1. Stop command detection (e.g. "bot 别接话", "闭嘴")
                                        if is_stop_command(c):
                                            log_msg(f"检测到停用/闭嘴指令: '{c}'，立即清空连续追问会话")
                                            mark_seen(s, c)
                                            last_replied_user = ""
                                            last_bot_reply_ts = 0.0
                                            continue

                                        # 2. Trivial message / emoji reaction filter (显式呼叫指令绝对不拦截)
                                        if is_trivial_or_reaction(c) and not is_explicit:
                                            log_msg(f"过滤非实质提问消息 (表情/标点/附和): '{c}'，不触发回复")
                                            mark_seen(s, c)
                                            continue

                                        # 3. Third-person mention correction (显式呼叫指令绝对不降级)
                                        if is_m and not is_explicit and not is_genuine_mention(c, bot_names):
                                            log_msg(f"更正误报呼叫 (第三人称提及而非召唤): '{c}' -> is_m设为False")
                                            is_m = False

                                        # 4. Follow-up intent verification
                                        valid_followup = False
                                        if session_active and (s == last_replied_user or last_replied_user in s or s in last_replied_user) and is_f:
                                            if has_followup_intent(c):
                                                valid_followup = True
                                                log_msg(f"有效连续追问命中 (用户={s}, 30秒窗口内): '{c}'")
                                            else:
                                                log_msg(f"追问窗口内但缺少明确追问疑问/指令意图: '{c}' -> 跳过回复")

                                        # 5. Quote bot verification
                                        valid_quote_reply = False
                                        if has_q:
                                            q_s = str(q_info.get("quoted_sender", "")).lower()
                                            if any(bn in q_s for bn in ["bot", "albot", "aibot", "微信小号"]) or q_s == "":
                                                if has_followup_intent(c) or (len(c) >= 6 and not is_trivial_or_reaction(c)):
                                                    valid_quote_reply = True
                                                    log_msg(f"用户引用 bot 消息并提出实质内容: '{c}' -> 触发回复")
                                                else:
                                                    log_msg(f"用户引用 bot 消息但仅为简短附和/闲聊: '{c}' -> 跳过回复")

                                        should_reply = is_explicit or (trigger_mode == "all") or is_m or valid_followup or valid_quote_reply
                                        log_msg(f"消息队列提取: sender={s}, content={c[:30]}, is_explicit={is_explicit}, is_m={is_m}, valid_f={valid_followup}, valid_q={valid_quote_reply}, should_reply={should_reply}")

                                        if should_reply:
                                            if isinstance(img_info, dict) and img_info.get("has_image") and img_info.get("_crop_box"):
                                                try:
                                                    cb = tuple(img_info["_crop_box"])
                                                    buf = io.BytesIO()
                                                    chat_img.crop(cb).save(buf, format="PNG")
                                                    img_info["_img_bytes"] = buf.getvalue()
                                                except Exception:
                                                    pass
                                            reply_candidates.append({
                                                "sender": s,
                                                "content": c,
                                                "quote": q_info,
                                                "image": img_info,
                                                "is_explicit": is_explicit,
                                                "is_direct": bool(is_explicit or is_m or valid_quote_reply),
                                            })
                                        else:
                                            # 不需要回复的消息，直接标记为已处理
                                            mark_seen(s, c)

                                    # 仅在视觉解析成功后才更新 prev_crop，防止超时/503报错吞掉群友消息
                                    if new_msg_count == 0:
                                        raw_diff = ImageChops.difference(new_crop, prev_crop)
                                        m = raw_diff.point(lambda p: 255 if p > 8 else 0).filter(ImageFilter.MaxFilter(5))
                                        if anim_mask is not None:
                                            m = ImageChops.lighter(anim_mask, m)
                                        if ImageStat.Stat(m).mean[0] < 115:
                                            anim_mask = m
                                            log_msg("检测到画面内原地动图/表情包闪烁 (无新消息)，已自动屏蔽动图区域像素波动")
                                    else:
                                        anim_mask = None
                                    prev_crop = new_crop

                                    if reply_candidates:
                                        # 所有直接提问 (@bot / 引用bot) 全部按时间顺序入队；纯追问取最新一条入队
                                        direct_targets = [t for t in reply_candidates if t.get("is_direct")]
                                        targets_to_enqueue = direct_targets if direct_targets else [reply_candidates[-1]]
                                        for cand in reply_candidates:
                                            if cand not in targets_to_enqueue:
                                                mark_seen(cand["sender"], cand["content"])
                                        for t in targets_to_enqueue:
                                            if not is_in_task_queue(t["sender"], t["content"]):
                                                if len(targets_to_enqueue) > 1 or len(reply_task_queue) > 0:
                                                    t["multi_user"] = True
                                                reply_task_queue.append(t)
                                                log_msg(f"[任务队列] 新提问入队: sender={t['sender']}, content={t['content'][:25]} (当前队列长度: {len(reply_task_queue)})")
                                except Exception as ke:
                                    log_msg(f"消息处理异常 (保留 prev_crop 以便下轮立即重试): {ke}")
                        else:
                            prev_crop = cur_crop

                    # 阶段二 (Consumer)：从 FIFO 任务队列取出队首任务执行回复
                    if reply_task_queue:
                        if len(reply_task_queue) > 1:
                            for item in reply_task_queue:
                                item["multi_user"] = True
                        target = reply_task_queue.popleft()
                        s = target["sender"]
                        c = target["content"]
                        log_msg(f"[任务队列] 开始处理队首任务 (剩余排队: {len(reply_task_queue)} 条): sender={s}, content={c[:30]}")

                        t_img = target.get("image") or {}
                        if isinstance(t_img, dict) and t_img.get("has_image"):
                            try:
                                if t_img.get("_img_bytes"):
                                    with open("/tmp/wechat_last_photo.png", "wb") as pf:
                                        pf.write(t_img["_img_bytes"])
                                    log_msg(f"已从队列缓存恢复待回复消息图片至 /tmp/wechat_last_photo.png ({len(t_img['_img_bytes'])} 字节)")
                                if not reply_task_queue and "表情" not in str(t_img.get("image_type", "")) and t_img.get("_screen_pos"):
                                    spos = t_img["_screen_pos"]
                                    extract_image_via_clipboard(spos[0], spos[1], "/tmp/wechat_last_photo.png")
                            except Exception as ie:
                                log_msg(f"按需提取消息图片异常: {ie}")

                        def check_new_msg_during_thinking():
                            if prev_crop is None:
                                return False
                            img_chk, _ = capture_screen_pil()
                            chk_crop = img_chk.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                            d_chk = ImageChops.difference(chk_crop, prev_crop)
                            if anim_mask is not None:
                                d_chk.paste(0, mask=anim_mask)
                            m_chk = ImageStat.Stat(d_chk).mean[0]
                            if m_chk > 1.2:
                                log_msg(f"检测到模型思考期间群聊有新消息插入 (diff={m_chk:.2f})，本条发送后将立即扫描入队！")
                                return True
                            return False

                        recent_history = get_recent(limit=15)
                        need_prefix = bool(target.get("multi_user") or len(reply_task_queue) > 0)
                        success, sent_text, had_concurrent_msg = safe_generate_and_reply(
                            target,
                            cfg,
                            recent_history,
                            prefix_sender=need_prefix,
                            pre_send_check_fn=check_new_msg_during_thinking,
                        )
                        if success:
                            last_recorded_text = sent_text
                            last_reply_text = sent_text
                            last_bot_reply_ts = time.time()
                            last_replied_user = s
                            log_msg(f"更新追问会话状态: 用户={last_replied_user}, 30秒追问窗口开启中")
                            time.sleep(0.7)
                            if had_concurrent_msg:
                                # 思考期间有人发了新消息！不覆盖 prev_crop，立即在下一轮触发视觉扫描把新消息排入队列
                                force_rescan = True
                                last_send_ts = 0.0
                                last_check_reply_ts = 0.0
                            else:
                                img3, _ = capture_screen_pil()
                                prev_crop = img3.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                                anim_mask = None
                                last_send_ts = time.time()
                        else:
                            log_msg("本次回复未成功，保持未标记已读状态，重置画面快照触发下轮重试")
                            prev_crop = None
                else:
                    prev_crop = None
                    anim_mask = None
        except Exception as e:
            log_msg(f"Monitor error: {e}")

        time.sleep(0.5 if reply_task_queue or force_rescan else 1.2)


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>管理员登录 - WeChat Bot 控制台</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0b0f19; color: #f1f5f9; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; }
  .card { background: rgba(30, 41, 59, 0.75); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 16px; padding: 36px 32px; width: 100%; max-width: 400px; box-shadow: 0 20px 45px rgba(0, 0, 0, 0.6); }
  .logo { font-size: 42px; text-align: center; margin-bottom: 12px; }
  h1 { font-size: 22px; text-align: center; font-weight: 700; margin-bottom: 6px; background: linear-gradient(135deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  p.sub { font-size: 13px; color: #94a3b8; text-align: center; margin-bottom: 24px; }
  .form-group { margin-bottom: 18px; }
  label { display: block; font-size: 13px; font-weight: 600; color: #cbd5e1; margin-bottom: 8px; }
  input[type="password"] { width: 100%; padding: 12px 14px; background: #090d16; border: 1px solid #334155; border-radius: 8px; color: #fff; font-size: 15px; outline: none; transition: border 0.2s, box-shadow 0.2s; }
  input[type="password"]:focus { border-color: #38bdf8; box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2); }
  button { width: 100%; padding: 12px; background: linear-gradient(135deg, #0284c7, #2563eb); border: none; border-radius: 8px; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; transition: transform 0.1s, opacity 0.2s; }
  button:hover { opacity: 0.92; }
  button:active { transform: scale(0.99); }
  .err { display: none; background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; color: #fca5a5; padding: 10px; border-radius: 8px; font-size: 13px; text-align: center; margin-bottom: 16px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">🤖</div>
  <h1>WeChat Bot 控制台</h1>
  <p class="sub">Oracle Singapore • MAGA 智能助手</p>
  <div id="err" class="err"></div>
  <form onsubmit="handleLogin(event)">
    <div class="form-group">
      <label for="pwd">管理员密码</label>
      <input type="password" id="pwd" placeholder="请输入管理员密码..." autofocus required>
    </div>
    <button type="submit" id="btn">验证并登录</button>
  </form>
</div>
<script>
async function handleLogin(e) {
  e.preventDefault();
  const btn = document.getElementById('btn');
  const err = document.getElementById('err');
  const pwd = document.getElementById('pwd').value;
  btn.disabled = true;
  btn.innerText = '正在验证...';
  err.style.display = 'none';
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password: pwd})
    });
    const d = await res.json();
    if (d.ok) {
      window.location.href = '/';
    } else {
      err.innerText = d.error || '密码错误';
      err.style.display = 'block';
      btn.disabled = false;
      btn.innerText = '验证并登录';
    }
  } catch (ex) {
    err.innerText = '网络连接异常，请重试';
    err.style.display = 'block';
    btn.disabled = false;
    btn.innerText = '验证并登录';
  }
}
</script>
</body>
</html>
"""


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WeChat Bot 运维控制台 (Oracle SG)</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif; background: #0b0f19; color: #e2e8f0; margin: 0; padding: 16px; }
  .header { max-width: 1300px; margin: 0 auto 16px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; padding-bottom: 12px; border-bottom: 1px solid #1e293b; }
  .title-group { display: flex; align-items: center; gap: 10px; }
  h1 { font-size: 20px; font-weight: 700; margin: 0; background: linear-gradient(135deg, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .status-badges { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 9999px; font-size: 12px; font-weight: 600; }
  .badge-ok { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
  .badge-wait { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
  .badge-err { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
  .badge-info { background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); }
  .btn-logout { background: transparent; border: 1px solid #475569; color: #94a3b8; padding: 4px 10px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  .btn-logout:hover { color: #f87171; border-color: #ef4444; }

  .wrap { max-width: 1300px; margin: 0 auto; display: grid; grid-template-columns: 1.15fr 0.85fr; gap: 16px; }
  @media (max-width: 980px) { .wrap { grid-template-columns: 1fr; } }

  .card { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border-radius: 14px; padding: 16px; border: 1px solid #334155; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3); }
  .card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
  h2 { margin: 0; font-size: 16px; font-weight: 600; color: #38bdf8; display: flex; align-items: center; gap: 6px; }

  /* Screen area */
  .view-switch { display: flex; gap: 6px; }
  .view-btn { padding: 4px 10px; font-size: 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #94a3b8; cursor: pointer; }
  .view-btn.active { background: #0284c7; color: #fff; border-color: #38bdf8; }

  #screen-wrap { position: relative; width: 100%; border: 2px solid #475569; border-radius: 8px; overflow: hidden; background: #000; cursor: crosshair; user-select: none; }
  #screen-img { display: block; width: 100%; height: auto; }
  #coord-hud { position: absolute; bottom: 8px; right: 8px; background: rgba(0, 0, 0, 0.75); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-family: monospace; color: #38bdf8; pointer-events: none; border: 1px solid rgba(56, 189, 248, 0.3); }
  .ripple { position: absolute; border: 2px solid #ef4444; border-radius: 50%; pointer-events: none; animation: rip 0.4s ease-out forwards; }
  @keyframes rip { from { width: 6px; height: 6px; opacity: 1; transform: translate(-50%, -50%); } to { width: 34px; height: 34px; opacity: 0; transform: translate(-50%, -50%); } }

  /* Toolbar */
  .toolbar { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
  .tool-btn { flex: 1; min-width: 90px; padding: 8px 10px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: 0.15s; display: inline-flex; align-items: center; justify-content: center; gap: 4px; }
  .btn-danger { background: #dc2626; color: #fff; }
  .btn-danger:hover { background: #b91c1c; }
  .btn-warning { background: #d97706; color: #fff; }
  .btn-warning:hover { background: #b45309; }
  .btn-primary { background: #0284c7; color: #fff; }
  .btn-primary:hover { background: #0369a1; }
  .btn-secondary { background: #334155; color: #e2e8f0; }
  .btn-secondary:hover { background: #475569; }

  /* Tabs */
  .tabs { display: flex; border-bottom: 1px solid #334155; margin-bottom: 14px; gap: 4px; }
  .tab { padding: 8px 12px; font-size: 13px; font-weight: 600; color: #94a3b8; cursor: pointer; border-bottom: 2px solid transparent; }
  .tab.active { color: #38bdf8; border-color: #38bdf8; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Form controls */
  label { display: block; font-size: 12px; font-weight: 600; color: #94a3b8; margin-top: 10px; margin-bottom: 4px; }
  input, textarea, select { width: 100%; box-sizing: border-box; padding: 8px 10px; border-radius: 6px; border: 1px solid #475569; background: #090d16; color: #f8fafc; font-size: 13px; outline: none; }
  input:focus, textarea:focus, select:focus { border-color: #38bdf8; }
  .action-btn { width: 100%; margin-top: 12px; padding: 10px; background: #0284c7; color: #fff; border: none; border-radius: 6px; font-weight: 600; cursor: pointer; }
  .action-btn:hover { background: #0369a1; }

  .log-box { background: #050811; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 11.5px; line-height: 1.5; padding: 10px; height: 320px; overflow-y: auto; border-radius: 6px; border: 1px solid #1e293b; color: #cbd5e1; white-space: pre-wrap; word-break: break-all; }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #1e293b; border: 1px solid #38bdf8; color: #fff; padding: 10px 16px; border-radius: 8px; font-size: 13px; box-shadow: 0 10px 20px rgba(0,0,0,0.5); opacity: 0; transform: translateY(10px); transition: all 0.25s; pointer-events: none; z-index: 9999; }
  .toast.show { opacity: 1; transform: translateY(0); }
</style>
</head>
<body>

<div class="header">
  <div class="title-group">
    <h1>🤖 WeChat Bot 实时监控与控制中枢</h1>
  </div>
  <div class="status-badges">
    <span id="badge-wechat" class="badge badge-wait">微信检测中...</span>
    <span id="badge-bot" class="badge badge-ok">Bot 在线</span>
    <span id="badge-autoreply" class="badge badge-ok">自动回复: 开</span>
    <span class="badge badge-info">:99 (1280x800)</span>
    <button class="btn-logout" onclick="logout()">退出登录</button>
  </div>
</div>

<div class="wrap">
  <!-- Left: Live Screen -->
  <div class="card">
    <div class="card-header">
      <h2>🖥️ 微信实时屏幕 (点击画面可直接在云端微信内操作)</h2>
      <div class="view-switch">
        <button id="btn-view-win" class="view-btn active" onclick="switchView('win')">🖼️ 微信窗口</button>
        <button id="btn-view-full" class="view-btn" onclick="switchView('screen')">🖥️ 全桌面</button>
      </div>
    </div>

    <div id="screen-wrap" onmousemove="handleMouseMove(event)" onmouseleave="handleMouseLeave()">
      <img id="screen-img" src="/api/win.png?t=0" onclick="handleScreenClick(event)">
      <div id="coord-hud">X: 0, Y: 0</div>
    </div>

    <!-- Quick action toolbar -->
    <div class="toolbar">
      <button class="tool-btn btn-warning" onclick="dismissModal()" title="关闭退出登录确认模态弹窗，解决输入框锁定">🛡️ 一键解除退出弹窗</button>
      <button class="tool-btn btn-primary" onclick="forceRefreshScreen()" title="立即抓取最新云端截图">🔄 立即刷新</button>
      <button class="tool-btn btn-secondary" onclick="sendVirtualKey('Clear')" title="清空当前输入框残留字符">🧹 清空输入框</button>
      <button class="tool-btn btn-secondary" onclick="sendVirtualKey('Enter')" title="向当前焦点窗口发送回车键">⏎ 发送回车</button>
      <button class="tool-btn btn-secondary" onclick="sendVirtualKey('Escape')" title="发送 Escape 键关闭上下文菜单">⎋ 发送 Esc</button>
    </div>
  </div>

  <!-- Right: Tabs & Controls -->
  <div class="card">
    <div class="tabs">
      <div class="tab active" onclick="showTab(0)">💬 手动发消息</div>
      <div class="tab" onclick="showTab(1)">🤖 机器人控制</div>
      <div class="tab" onclick="showTab(2)">🔍 搜索 Key 池</div>
      <div class="tab" onclick="showTab(3)">🛠️ 进程管理</div>
      <div class="tab" onclick="showTab(4)">📜 实时日志</div>
    </div>

    <!-- Tab 0: Manual Send -->
    <div class="tab-content active" id="tab-0">
      <label>手动发送文本到当前已打开的聊天窗口：</label>
      <textarea id="manual-text" rows="4" placeholder="在此输入文字，点击下方按钮将直接自动粘贴并回车发送到当前群聊..."></textarea>
      <button class="action-btn" onclick="sendManualText()">🚀 立即发送到当前群聊</button>

      <div style="margin-top:14px; font-size:12px; color:#94a3b8;">
        快捷快捷发送预设测试语：
        <div style="display:flex; gap:6px; margin-top:6px; flex-wrap:wrap;">
          <button class="view-btn" onclick="fillTest('bot 状态汇报')">bot 状态汇报</button>
          <button class="view-btn" onclick="fillTest('bot 评价一下')">bot 评价一下</button>
          <button class="view-btn" onclick="fillTest('bot 今天有什么热点')">bot 今天有什么热点</button>
        </div>
      </div>
    </div>

    <!-- Tab 1: Bot Config -->
    <div class="tab-content" id="tab-1">
      <label>AI 自动回复开关：</label>
      <select id="auto-reply">
        <option value="true">开启自动回复 (根据消息特征自动调用模型)</option>
        <option value="false">暂停自动回复 (仅保留人工手动控制与监控)</option>
      </select>

      <label>群聊触发模式：</label>
      <select id="trigger-mode">
        <option value="mention_only">仅被 @ 或呼叫小号名字时回复 (推荐)</option>
        <option value="all">群内所有新消息均自动回复</option>
      </select>

      <label>小号触发关键词 (英文逗号分隔)：</label>
      <input type="text" id="bot-names" placeholder="@, bot, AIbot, Bot, BOT">

      <label>模型回复发散度 Temperature (0.1 ~ 1.5，日常群聊推荐 0.75)：</label>
      <input type="number" id="temperature" step="0.05" min="0.1" max="1.5" placeholder="0.75">

      <label>高德地图 WebKey (查美食与出行)：</label>
      <input type="text" id="amap-key" placeholder="可选高德 Web 服务 Key">

      <label>机器人人设 System Prompt：</label>
      <textarea id="sys-prompt" rows="3"></textarea>

      <label>修改 Web 控制台管理员密码 (留空则不修改)：</label>
      <input type="password" id="new-pwd" placeholder="输入新的管理员密码...">

      <button class="action-btn" onclick="saveConfig()">💾 保存配置</button>
    </div>

    <!-- Tab 2: Serper Key Pool -->
    <div class="tab-content" id="tab-2">
      <div class="card-header" style="margin-bottom:6px;">
        <h2>🔍 Serper Google 搜索 API Key 池</h2>
        <button class="view-btn" onclick="loadSerperKeys()">🔄 刷新列表</button>
      </div>
      <p style="font-size:12px; color:#94a3b8; margin-bottom:12px; line-height:1.5;">
        系统接入了 Serper.dev Google 全网检索。每个免费 Key 拥有 2,500 次查询额度。当主力 Key 额度耗尽（HTTP 403）时，系统会自动将其废弃并无缝切入下一个后备 Key！
      </p>

      <label style="font-weight:600; margin-bottom:6px; display:block;">当前可用 Key 列表 (按优先调用顺序排列)：</label>
      <div id="serper-keys-list" style="margin-bottom:14px;">正在加载 Key 池...</div>

      <label style="font-weight:600; margin-bottom:6px; display:block;">➕ 添加新的后备 Serper API Key：</label>
      <textarea id="input-new-serper-keys" rows="3" placeholder="在此粘贴新申请的 Serper API Key (支持一次输入一个或多个，换行、空格或逗号分隔即可)..."></textarea>
      <button class="action-btn" onclick="addSerperKeys()">🚀 立即添加到后备池</button>

      <div id="serper-exhausted-list" style="margin-top:14px; display:none;"></div>
    </div>

    <!-- Tab 3: Process & Recovery -->
    <div class="tab-content" id="tab-3">
      <label>微信与系统进程状态：</label>
      <div id="proc-info" style="font-size:12px; background:#050811; padding:8px 10px; border-radius:6px; border:1px solid #1e293b; font-family:monospace; margin-bottom:12px;">加载中...</div>

      <div style="display:flex; flex-direction:column; gap:8px;">
        <button class="tool-btn btn-warning" onclick="recoverUI()">🩺 一键运行 UI 排错与防卡顿自愈</button>
        <button class="tool-btn btn-danger" onclick="restartWechat()">🔄 重启微信主进程 (kill & relaunch)</button>
        <button class="tool-btn btn-secondary" onclick="restartDaemon()">⚡ 重启 wechat-bot 后台服务</button>
      </div>
    </div>

    <!-- Tab 4: Realtime Logs -->
    <div class="tab-content" id="tab-4">
      <div class="card-header" style="margin-bottom:6px;">
        <span style="font-size:12px; color:#94a3b8;">自动更新 (2s/次)</span>
        <button class="view-btn" onclick="fetchLogs()">🔄 刷新日志</button>
      </div>
      <div id="logs" class="log-box">正在加载日志...</div>
    </div>
  </div>
</div>

<div id="toast" class="toast">操作成功</div>

<script>
let currentView = 'win';
let windowMeta = {x: 0, y: 0, width: 1280, height: 800};

function toast(msg) {
  const el = document.getElementById('toast');
  el.innerText = msg;
  el.className = 'toast show';
  setTimeout(() => { el.className = 'toast'; }, 2200);
}

function switchView(view) {
  currentView = view;
  document.getElementById('btn-view-win').className = 'view-btn' + (view === 'win' ? ' active' : '');
  document.getElementById('btn-view-full').className = 'view-btn' + (view === 'screen' ? ' active' : '');
  forceRefreshScreen();
}

function forceRefreshScreen() {
  const img = document.getElementById('screen-img');
  const endpoint = currentView === 'win' ? '/api/win.png' : '/api/screen.png';
  img.src = endpoint + '?t=' + Date.now();
}

function showTab(idx) {
  const tabs = document.querySelectorAll('.tab');
  const contents = document.querySelectorAll('.tab-content');
  tabs.forEach((t, i) => t.className = 'tab' + (i === idx ? ' active' : ''));
  contents.forEach((c, i) => c.className = 'tab-content' + (i === idx ? ' active' : ''));
}

function getActualCoords(ev) {
  const img = document.getElementById('screen-img');
  const rect = img.getBoundingClientRect();
  const clickFractionX = (ev.clientX - rect.left) / rect.width;
  const clickFractionY = (ev.clientY - rect.top) / rect.height;

  if (currentView === 'screen') {
    return {
      x: Math.round(clickFractionX * 1280),
      y: Math.round(clickFractionY * 800)
    };
  } else {
    const pad = 12;
    const cropX = Math.max(0, windowMeta.x - pad);
    const cropY = Math.max(0, windowMeta.y - pad);
    const cropW = windowMeta.width + pad * 2;
    const cropH = windowMeta.height + pad * 2;
    return {
      x: Math.round(cropX + clickFractionX * cropW),
      y: Math.round(cropY + clickFractionY * cropH)
    };
  }
}

function handleMouseMove(ev) {
  const pt = getActualCoords(ev);
  document.getElementById('coord-hud').innerText = `X: ${pt.x}, Y: ${pt.y}`;
}

function handleMouseLeave() {
  document.getElementById('coord-hud').innerText = 'X: 0, Y: 0';
}

async function handleScreenClick(ev) {
  const pt = getActualCoords(ev);
  const wrap = document.getElementById('screen-wrap');
  const rect = wrap.getBoundingClientRect();
  const rip = document.createElement('div');
  rip.className = 'ripple';
  rip.style.left = (ev.clientX - rect.left) + 'px';
  rip.style.top = (ev.clientY - rect.top) + 'px';
  wrap.appendChild(rip);
  setTimeout(() => rip.remove(), 450);

  try {
    await fetch('/api/click', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({x: pt.x, y: pt.y})
    });
    toast(`已点击 (${pt.x}, ${pt.y})`);
    setTimeout(forceRefreshScreen, 350);
  } catch (e) {
    toast('点击指令发送失败');
  }
}

async function dismissModal() {
  try {
    const res = await fetch('/api/dismiss_popup', {method: 'POST'});
    const d = await res.json();
    toast(d.message || '已触发关闭弹窗');
    setTimeout(forceRefreshScreen, 400);
  } catch (e) { toast('关闭弹窗异常'); }
}

async function sendVirtualKey(key) {
  try {
    await fetch('/api/key', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key})
    });
    toast(`已发送按键: ${key}`);
    setTimeout(forceRefreshScreen, 400);
  } catch (e) { toast('按键发送失败'); }
}

async function sendManualText() {
  const el = document.getElementById('manual-text');
  const text = el.value.trim();
  if (!text) return toast('请输入发送文本');
  try {
    const res = await fetch('/api/send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    });
    const d = await res.json();
    if (d.ok) {
      toast('文本已成功发送至群聊！');
      el.value = '';
      setTimeout(forceRefreshScreen, 600);
    } else {
      toast('发送失败: ' + (d.message || '未知错误'));
    }
  } catch (e) { toast('网络错误'); }
}

function fillTest(str) {
  document.getElementById('manual-text').value = str;
}

async function recoverUI() {
  toast('正在执行 UI 排错与自愈...');
  try {
    const res = await fetch('/api/recover_ui', {method: 'POST'});
    const d = await res.json();
    toast(d.message || '自愈完成');
    setTimeout(forceRefreshScreen, 500);
  } catch (e) { toast('自愈执行失败'); }
}

async function restartWechat() {
  if (!confirm('确定要重启云端微信主进程吗？')) return;
  toast('正在重启微信...');
  try {
    await fetch('/api/restart_wechat', {method: 'POST'});
    toast('微信正在后台重新启动，请稍候 5 秒...');
    setTimeout(forceRefreshScreen, 5000);
  } catch (e) { toast('重启命令发送失败'); }
}

async function restartDaemon() {
  if (!confirm('确定要重启 wechat-bot 机器人后台服务吗？')) return;
  toast('正在重启服务...');
  try {
    await fetch('/api/restart_daemon', {method: 'POST'});
    toast('服务已重启，页面即将重新连接...');
    setTimeout(() => window.location.reload(), 2500);
  } catch (e) { toast('服务重启异常'); }
}

async function saveConfig() {
  const auto_reply = document.getElementById('auto-reply').value === 'true';
  const trigger_mode = document.getElementById('trigger-mode').value;
  const bot_names = document.getElementById('bot-names').value.split(',').map(s => s.trim()).filter(Boolean);
  const temperature = parseFloat(document.getElementById('temperature').value) || 0.75;
  const amap_api_key = document.getElementById('amap-key').value.trim();
  const system_prompt = document.getElementById('sys-prompt').value.trim();
  const new_password = document.getElementById('new-pwd').value.trim();

  const payload = {auto_reply, trigger_mode, bot_names, temperature, amap_api_key, system_prompt};
  if (new_password) payload.admin_password = new_password;

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const d = await res.json();
    if (d.ok) {
      toast('配置已成功保存！');
      document.getElementById('new-pwd').value = '';
    } else {
      toast('保存失败: ' + (d.error || ''));
    }
  } catch (e) { toast('保存网络异常'); }
}

async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    if (res.status === 401) {
      window.location.href = '/login';
      return;
    }
    const d = await res.json();

    const wechatBadge = document.getElementById('badge-wechat');
    if (d.logged_in) {
      wechatBadge.className = 'badge badge-ok';
      wechatBadge.innerText = '✅ 微信在线';
    } else {
      wechatBadge.className = 'badge badge-wait';
      wechatBadge.innerText = '⏳ 等待登录';
    }

    const autoBadge = document.getElementById('badge-autoreply');
    if (d.config && d.config.auto_reply !== false) {
      autoBadge.className = 'badge badge-ok';
      autoBadge.innerText = '自动回复: 开';
    } else {
      autoBadge.className = 'badge badge-wait';
      autoBadge.innerText = '自动回复: 关';
    }

    if (d.windows && d.windows.length > 0) {
      windowMeta = d.windows[0];
    }

    const proc = document.getElementById('proc-info');
    proc.innerText = `WeChat PID: ${d.wechat_pid || '运行中'} | 窗口尺寸: ${windowMeta.width}x${windowMeta.height} (${windowMeta.x}, ${windowMeta.y})\n运行时长: ${d.uptime || '持续在役'} | 监控线程: Active`;

    const logBox = document.getElementById('logs');
    if (d.logs && d.logs.length > 0) {
      logBox.innerText = d.logs.join('\\n');
      logBox.scrollTop = logBox.scrollHeight;
    }
  } catch (e) {}
}

async function loadInitialConfig() {
  try {
    const res = await fetch('/api/status');
    const d = await res.json();
    if (d.config) {
      document.getElementById('auto-reply').value = String(d.config.auto_reply !== false);
      document.getElementById('trigger-mode').value = d.config.trigger_mode || 'mention_only';
      document.getElementById('bot-names').value = (d.config.bot_names || ['AIbot', 'Albot', 'aibot', 'bot']).join(', ');
      document.getElementById('temperature').value = d.config.temperature !== undefined ? d.config.temperature : 0.75;
      document.getElementById('amap-key').value = d.config.amap_api_key || '';
      document.getElementById('sys-prompt').value = d.config.system_prompt || '';
    }
  } catch (e) {}
}

async function loadSerperKeys() {
  try {
    const res = await fetch('/api/serper_keys');
    const d = await res.json();
    if (d.ok) {
      renderSerperKeys(d);
    }
  } catch (e) {}
}

function renderSerperKeys(d) {
  const container = document.getElementById('serper-keys-list');
  if (!d.active_keys || d.active_keys.length === 0) {
    container.innerHTML = '<div style="color:#ef4444; font-size:13px; padding:10px; background:rgba(239,68,68,0.12); border-radius:6px; border:1px solid rgba(239,68,68,0.3);">⚠️ 当前无可用 Key，联网搜索将自动降级使用维基百科与必应搜索</div>';
    return;
  }
  let html = '';
  d.active_keys.forEach((k, idx) => {
    const isPri = idx === 0;
    const tag = isPri ? '<span class="badge badge-ok">当前主力 (优先调用)</span>' : `<span class="badge badge-info">后备 #${idx}</span>`;
    html += `
      <div style="display:flex; align-items:center; justify-content:space-between; background:#050811; border:1px solid #1e293b; padding:8px 12px; border-radius:6px; margin-bottom:6px;">
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-family:monospace; font-size:13px; color:#f1f5f9;">${k.masked}</span>
          ${tag}
        </div>
        <button class="tool-btn btn-danger" style="flex:none; min-width:auto; padding:3px 10px; font-size:11px;" onclick="removeSerperKey('${k.key}')">🗑️ 废弃/移除</button>
      </div>
    `;
  });
  container.innerHTML = html;

  const exhContainer = document.getElementById('serper-exhausted-list');
  if (d.exhausted_keys && d.exhausted_keys.length > 0) {
    let exhHtml = '<label style="margin-top:14px; color:#94a3b8; font-size:12px; font-weight:600; display:block;">已自动废弃的失效/用尽 Key 记录 (最近20条)：</label>';
    d.exhausted_keys.forEach(e => {
      exhHtml += `
        <div style="font-size:11px; color:#64748b; font-family:monospace; padding:5px 0; border-bottom:1px dashed #1e293b;">
          ❌ ${e.masked || e.key} — ${e.discarded_at} (${e.reason || '额度用完'})
        </div>
      `;
    });
    exhContainer.innerHTML = exhHtml;
    exhContainer.style.display = 'block';
  } else {
    exhContainer.style.display = 'none';
  }
}

async function addSerperKeys() {
  const txt = document.getElementById('input-new-serper-keys').value.trim();
  if (!txt) {
    toast('请输入要添加的 Serper Key');
    return;
  }
  try {
    const res = await fetch('/api/serper_keys/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: txt})
    });
    const d = await res.json();
    if (d.ok) {
      toast(`成功添加 ${d.added || 1} 个 Key 到后备池！`);
      document.getElementById('input-new-serper-keys').value = '';
      loadSerperKeys();
    } else {
      toast('添加失败: ' + (d.error || ''));
    }
  } catch (e) { toast('添加网络异常'); }
}

async function removeSerperKey(key) {
  if (!confirm('确定要从后备池中移除此 Key 吗？')) return;
  try {
    const res = await fetch('/api/serper_keys/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key})
    });
    const d = await res.json();
    if (d.ok) {
      toast('已成功移除 Key');
      loadSerperKeys();
    }
  } catch (e) { toast('移除网络异常'); }
}

function logout() {
  window.location.href = '/logout';
}

loadInitialConfig();
loadSerperKeys();
fetchStatus();
setInterval(fetchStatus, 2500);
setInterval(forceRefreshScreen, 3000);
</script>
</body>
</html>
"""


def get_admin_token(pwd):
    return hashlib.sha256((pwd + "_wechat_salt_2026").encode("utf-8")).hexdigest()


class BotHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, code=200, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _is_authenticated(self):
        cfg = load_config()
        admin_pwd = cfg.get("admin_password", "Lin2026@k9x2!")
        expected_token = get_admin_token(admin_pwd)

        # 1. 检查 Cookie
        cookie_header = self.headers.get("Cookie", "")
        if f"bot_session={expected_token}" in cookie_header:
            return True

        # 2. 检查 HTTP Basic Auth
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                raw = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
                parts = raw.split(":", 1)
                if len(parts) == 2 and parts[1] == admin_pwd:
                    return True
            except Exception:
                pass

        # 3. 检查 Query Param (?token=... or ?pwd=...)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "token" in qs and qs["token"][0] == expected_token:
            return True
        if "pwd" in qs and qs["pwd"][0] == admin_pwd:
            return True

        return False

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", "bot_session=deleted; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.send_header("Location", "/")
            self.end_headers()
            return

        if parsed.path == "/login":
            body = LOGIN_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        is_auth = self._is_authenticated()

        if parsed.path in ("/", "/index.html"):
            if not is_auth:
                body = LOGIN_HTML.encode("utf-8")
            else:
                body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 针对 API 与图片接口的鉴权防护
        if not is_auth:
            self._send_json({"ok": False, "error": "Unauthorized"}, code=401)
            return

        if parsed.path == "/api/screen.png":
            p = "/tmp/screen.png"
            if os.path.exists(p):
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
        elif parsed.path == "/api/win.png":
            p = "/tmp/wechat_win.png" if os.path.exists("/tmp/wechat_win.png") else "/tmp/screen.png"
            if os.path.exists(p):
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
        elif parsed.path == "/api/status":
            _, wins = capture_screen_pil()
            logged_in = bool(wins and wins[0]["width"] >= 550)
            cfg = load_config()
            safe_cfg = {k: v for k, v in cfg.items() if not k.endswith("_key") and k != "admin_password"}
            with state_lock:
                logs_copy = list(recent_logs)
            
            wechat_pid = ""
            try:
                wechat_pid = subprocess.check_output(["pgrep", "-f", "/usr/bin/wechat"]).decode("utf-8").strip().split("\n")[0]
            except Exception:
                pass

            self._send_json({
                "ok": True,
                "logged_in": logged_in,
                "wechat_pid": wechat_pid,
                "uptime": "Active",
                "windows": [{k: v for k, v in w.items() if k != "win"} for w in wins],
                "config": safe_cfg,
                "logs": logs_copy,
            })
        elif parsed.path == "/api/history":
            self._send_json({"ok": True, "history": get_recent(30)})
        elif parsed.path == "/api/serper_keys":
            from plugins.serper_pool import get_serper_pool_status
            self._send_json(get_serper_pool_status())
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        except Exception:
            body = {}

        # 登录接口无需鉴权
        if parsed.path == "/api/login":
            pwd = body.get("password", "")
            cfg = load_config()
            admin_pwd = cfg.get("admin_password", "Lin2026@k9x2!")
            if pwd == admin_pwd:
                token = get_admin_token(admin_pwd)
                cookie = f"bot_session={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax"
                self._send_json({"ok": True, "token": token}, extra_headers={"Set-Cookie": cookie})
            else:
                self._send_json({"ok": False, "error": "密码错误，请重新输入"}, code=403)
            return

        # 其他所有 POST 接口必须经过认证
        if not self._is_authenticated():
            self._send_json({"ok": False, "error": "Unauthorized"}, code=401)
            return

        if parsed.path == "/api/click":
            x = int(body.get("x", 640))
            y = int(body.get("y", 400))
            x11_click(x, y)
            log_msg(f"控制台指令: 点击坐标 ({x}, {y})")
            self._send_json({"ok": True, "x": x, "y": y})
        elif parsed.path == "/api/send":
            text = body.get("text", "")
            ok, msg = x11_send_text(text)
            self._send_json({"ok": ok, "message": msg})
        elif parsed.path == "/api/dismiss_popup":
            dismissed = check_and_dismiss_logout_modal()
            if not dismissed:
                x11_click(702, 437)
            log_msg("控制台指令: 执行解除退出模态弹窗")
            self._send_json({"ok": True, "message": "已触发关闭退出登录弹窗"})
        elif parsed.path == "/api/key":
            key_name = body.get("key", "")
            d = display.Display(":99")
            try:
                if key_name == "Enter":
                    kc = d.keysym_to_keycode(XK.XK_Return)
                    xtest.fake_input(d, X.KeyPress, kc)
                    xtest.fake_input(d, X.KeyRelease, kc)
                elif key_name == "Escape":
                    kc = d.keysym_to_keycode(XK.XK_Escape)
                    xtest.fake_input(d, X.KeyPress, kc)
                    xtest.fake_input(d, X.KeyRelease, kc)
                elif key_name == "Clear":
                    ctrl_kc = d.keysym_to_keycode(XK.XK_Control_L)
                    a_kc = d.keysym_to_keycode(XK.XK_a)
                    bs_kc = d.keysym_to_keycode(XK.XK_BackSpace)
                    xtest.fake_input(d, X.KeyPress, ctrl_kc)
                    xtest.fake_input(d, X.KeyPress, a_kc)
                    xtest.fake_input(d, X.KeyRelease, a_kc)
                    xtest.fake_input(d, X.KeyRelease, ctrl_kc)
                    d.sync()
                    time.sleep(0.03)
                    xtest.fake_input(d, X.KeyPress, bs_kc)
                    xtest.fake_input(d, X.KeyRelease, bs_kc)
                d.sync()
            finally:
                d.close()
            log_msg(f"控制台指令: 发送按键 {key_name}")
            self._send_json({"ok": True, "key": key_name})
        elif parsed.path == "/api/recover_ui":
            recover_wechat_ui_state()
            self._send_json({"ok": True, "message": "UI 排错自愈机制已执行完毕"})
        elif parsed.path == "/api/restart_wechat":
            def _restart_wc():
                time.sleep(0.5)
                subprocess.call(["killall", "-9", "wechat"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2.0)
                env = os.environ.copy()
                env["DISPLAY"] = ":99"
                subprocess.Popen(["/usr/bin/wechat"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=_restart_wc, daemon=True).start()
            log_msg("控制台指令: 触发重启微信客户端")
            self._send_json({"ok": True, "message": "微信正在重启中..."})
        elif parsed.path == "/api/restart_daemon":
            def _restart_dm():
                time.sleep(0.8)
                subprocess.call(["sudo", "systemctl", "restart", "wechat-bot"])
            threading.Thread(target=_restart_dm, daemon=True).start()
            log_msg("控制台指令: 触发重启 wechat-bot 服务")
            self._send_json({"ok": True, "message": "机器人服务正在重启..."})
        elif parsed.path == "/api/config":
            cfg = load_config()
            for k in ("auto_reply", "trigger_mode", "bot_names", "system_prompt", "amap_api_key", "admin_password", "temperature"):
                if k in body:
                    cfg[k] = body[k]
            save_config(cfg)
            log_msg(f"控制台更新配置: trigger_mode={cfg.get('trigger_mode')}, auto_reply={cfg.get('auto_reply')}, temp={cfg.get('temperature')}")
            self._send_json({"ok": True})
        elif parsed.path == "/api/serper_keys/add":
            from plugins.serper_pool import add_serper_keys, get_serper_pool_status
            keys_input = body.get("keys") or body.get("key") or ""
            added_cnt, _ = add_serper_keys(keys_input)
            log_msg(f"控制台指令: 新增 {added_cnt} 个 Serper API Key 到后备池")
            self._send_json({"ok": True, "added": added_cnt, "pool": get_serper_pool_status()})
        elif parsed.path == "/api/serper_keys/remove":
            from plugins.serper_pool import remove_serper_key, get_serper_pool_status
            target_key = body.get("key", "").strip()
            removed, _ = remove_serper_key(target_key)
            masked = f"{target_key[:6]}...{target_key[-4:]}" if len(target_key) >= 10 else target_key
            log_msg(f"控制台指令: 手动移除 Serper API Key: {masked}")
            self._send_json({"ok": True, "removed": removed, "pool": get_serper_pool_status()})
        else:
            self.send_error(404)


if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", 18890), BotHTTPHandler)
    log_msg("HTTP 控制台已启动: http://127.0.0.1:18890")
    server.serve_forever()
