#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import base64
import io
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageStat
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
seen_msg_timestamps = {}
seen_content_timestamps = {}


def is_recent_duplicate(sender, content, window=180):
    now = time.time()
    c_clean = content.strip()
    s_clean = sender.strip()
    key_full = f"{s_clean}:::{c_clean}"
    if now - seen_msg_timestamps.get(key_full, 0) < window:
        return True
    if len(c_clean) > 3 and now - seen_content_timestamps.get(c_clean, 0) < 60:
        return True
    return False


def mark_seen(sender, content):
    now = time.time()
    c_clean = content.strip()
    s_clean = sender.strip()
    key_full = f"{s_clean}:::{c_clean}"
    seen_msg_timestamps[key_full] = now
    if c_clean:
        seen_content_timestamps[c_clean] = now
    if len(seen_msg_timestamps) > 300:
        cutoff = now - 600
        for k in [k for k, t in seen_msg_timestamps.items() if t < cutoff]:
            del seen_msg_timestamps[k]
        for k in [k for k, t in seen_content_timestamps.items() if t < cutoff]:
            del seen_content_timestamps[k]


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

    # 2. 显式 @bot 放行
    if re.search(r'@(?:Albot|albot|bot|Bot|BOT|AIbot|aibot|微信小号|\S+bot|\S+机器人)', c):
        return True
    if re.search(r'(?:^|\s)@(?:\S+)?', c):
        if not re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', c):
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


def extract_urls(text):
    if not text:
        return []
    return re.findall(r'https?://[^\s<>"\')]+', text)


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
                            return raw_link
                        m2 = re.search(rb'https://mp\.weixin\.qq\.com/s[a-zA-Z0-9_\-\.\/\?\=\&\%]+', chunk)
                        if m2:
                            raw_link = m2.group(0).decode("utf-8", errors="ignore").replace("&amp;", "&").rstrip("\\;&<>'\"")
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


def init_db():
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
        conn.close()
    except Exception as e:
        log_msg(f"Init DB error: {e}")


def save_msg(sender, content, is_bot=False, is_mentioned=False):
    content = (content or "").strip()
    if not content:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        ts = time.time()
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
                if cls and ("wechat" in cls[0].lower() or "wechat" in cls[1].lower()):
                    if geom.width >= 150 and geom.height >= 150:
                        coords = root.translate_coords(w, 0, 0)
                        wins.append({
                            "win": w,
                            "id": hex(w.id),
                            "name": w.get_wm_name() or "",
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
    wins.sort(key=lambda item: item["width"] * item["height"], reverse=True)
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


def x11_send_text(text):
    global last_send_ts, last_reply_text
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
    for sel in ("clipboard", "primary"):
        p = subprocess.Popen(
            ["/usr/local/bin/xclip", "-selection", sel, "-i"],
            stdin=subprocess.PIPE,
            env=env,
        )
        p.communicate(input=text.encode("utf-8"), timeout=3)

    click_x = main_w["x"] + int(main_w["width"] * 0.65)
    click_y = main_w["y"] + main_w["height"] - 75
    x11_click(click_x, click_y)
    time.sleep(0.18)

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
        time.sleep(0.25)

        xtest.fake_input(d, X.KeyPress, ret_kc)
        xtest.fake_input(d, X.KeyRelease, ret_kc)
        d.sync()
    finally:
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


def call_llm_chat(messages, tools=None, timeout=75):
    cfg = load_config()
    api_key = cfg.get("api_key") or cfg.get("gemini_api_key") or cfg.get("kimi_api_key", "")
    base_url = cfg.get("base_url") or cfg.get("gemini_base_url") or cfg.get("kimi_base_url", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    model = cfg.get("model") or cfg.get("gemini_model") or cfg.get("kimi_model", "gemini-4-argon")

    payload = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if "kimi" in base_url or "kimi" in model:
        headers["User-Agent"] = "KimiCLI/0.77"

    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

call_kimi_chat = call_llm_chat


def parse_recent_messages_with_vision(chat_img, bot_names, last_replied_user="", session_active=False):
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
        f"   - 必须满足以下条件之一：\n"
        f"     * 包含对 bot 的艾特，如 @bot、@AIbot、@Albot、@微信小号 等\n"
        f"     * 句子开头就是直接呼唤 bot 提出要求，如：“bot 查一下...”、“AIbot 帮我看下...”\n"
        f"   - 【严格禁忌】：群友在第三人称随口谈论提到 bot（例如“不用加bot你也会回”、“这个bot好逗”、“加bot干嘛”），是在与群友闲聊，绝对不是在呼叫 bot！is_mentioned 必须为 false！\n"
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
    res = call_kimi_chat(messages, timeout=60)
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
                                chat_img.crop(crop_box).save("/tmp/wechat_last_photo.png")
                                log_msg(f"成功定位并裁剪群聊中的真实图片: {crop_box} -> /tmp/wechat_last_photo.png")
                        except Exception as ce:
                            log_msg(f"裁剪图片失败: {ce}")
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


def generate_reply_with_search(sender, content, cfg, recent_history=None, quote_info=None, image_info=None):
    # 0. Fast-Path Direct Execution (0 Token, <100ms ultra-low latency)
    if plugin_manager:
        matched, fast_reply = plugin_manager.match_fast_path(content)
        if matched and fast_reply:
            log_msg(f"命中 Fast-Path 快速直通插件: '{content}' -> 直接返回预置数据")
            return fast_reply

    sys_prompt = cfg.get("system_prompt", "")
    cfg_model = cfg.get("model") or cfg.get("kimi_model", "")
    cfg_url = cfg.get("base_url") or cfg.get("kimi_base_url", "")
    is_kimi = "kimi" in cfg_model.lower() or "kimi" in cfg_url.lower()

    plugin_tools = plugin_manager.get_openai_tools() if plugin_manager else []
    if is_kimi:
        tools = plugin_tools + [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    else:
        tools = plugin_tools

    now_str = datetime.now(TZ_BJ).strftime("%Y年%m月%d日 %H:%M")

    history_str = ""
    if recent_history:
        lines = [f"[{h['time']}] {h['sender']}: {h['content']}" for h in recent_history]
        history_str = "【群聊最近上下文记录（供参考理解语境、代词与话题连贯性）】：\n" + "\n".join(lines) + "\n\n"

    system_instruction = (
        f"{sys_prompt}\n"
        f"【当前系统时间】：{now_str}（北京时间）。\n"
        f"{history_str}"
        f"【实时 API 工具调用指引（高优先级）】：\n"
        f"- 系统已接入丰富实时的本地与在线 API 工具库：\n"
        f"  • 查天气 (get_weather)：全球实况与未来1-14天逐日预报\n"
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
        f"【回答规范与要求】：\n"
        f"1. 回答篇幅根据问题需要自然展开即可，不需要死板限制在 1~3 句以内。简单问题简明直接，需要查询或解释的问题要把事情说清楚、回答透彻、内容充实有用。\n"
        f"2. 结合上面的最近聊天记录，准确理解群友话语中的代词（如‘这句话’、‘刚才说的’、‘他’、‘那个’等）以及话题脉络，保持连贯记忆。\n"
        f"3. 保持沉稳、自然、客观的朋友口吻，分寸感要好，不用太俏皮，严禁客服腔和AI味。严禁与群友抬杠、辩解或刻意戏谑调侃。\n"
        f"4. 直接输出面向群友的最终纯文本回答，严禁输出任何‘我查一下’、‘稍等’等过渡性碎碎念，绝对严禁输出任何代码、JSON、搜索函数名或 `$web_search` 等内部指令标签！"
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
    candidate_texts = [content]
    if quote_info and quote_info.get("has_quote"):
        candidate_texts.append(str(quote_info.get("quoted_content", "")))
    if recent_history:
        for h in reversed(recent_history[-5:]):
            candidate_texts.append(h.get("content", ""))

    found_urls = []
    for ct in candidate_texts:
        for u in extract_urls(ct):
            if u not in found_urls:
                found_urls.append(u)

    # 2. If no explicit HTTP link found in text, check for WeChat Article Card or quote with 🔗
    # Scan WeChat process memory to convert the card into the real mp.weixin.qq.com URL!
    if not found_urls:
        card_kw = ""
        for ct in candidate_texts:
            if "🔗" in ct or "http" in ct:
                card_kw = re.sub(r'.*?🔗\s*', '', ct)
                break
            m = re.search(r'\[分享文章卡片:\s*《(.*?)》', ct)
            if m:
                card_kw = m.group(1).strip()
                break
            if "“" in ct and "”" in ct and len(ct) >= 10:
                card_kw = ct
                break

        if not card_kw and any(k in content for k in ["链接", "文章", "卡片", "新闻", "内容", "评价", "看下", "怎么看"]):
            for ct in candidate_texts:
                if len(ct) >= 10 and not any(bn in ct for bn in ["bot", "albot"]):
                    card_kw = ct
                    break

        if card_kw:
            mem_url = find_article_link_in_memory(card_kw)
            if mem_url:
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
    if not fetched_any_article:
        card_title = ""
        card_source = ""
        for ct in candidate_texts:
            m = re.search(r'\[分享文章卡片:\s*《(.*?)》(?:[ \t]*来源:[ \t]*([^\]]+))?', ct)
            if m:
                card_title = m.group(1).strip()
                card_source = (m.group(2) or "").strip()
                break
            if "“" in ct and "”" in ct and len(ct) >= 12:
                card_title = ct.strip()
                break

        if card_title:
            user_query += (
                f"\n【重要任务：群友正在要求阅读与评价/分析分享的文章】：\n"
                f"- 分享文章标题：《{card_title}》\n"
                f"- 媒体/发布来源：{card_source or '权威新闻媒体'}\n"
                f"- 核心要求：群友正在要求你阅读并评价/分析这篇分享的文章！由于微信卡片只展示了部分标题，你【必须立即使用 $web_search 联网搜索工具】检索该文章的完整报道正文与真实事件细节！在完整阅读了真实正文后，再进行全面、客观、深入有依据的分析评价！绝对严禁仅凭标题凭空推测或说‘光从标题看’！\n"
            )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_query},
    ]

    final_reply = ""
    msg = {}
    for loop_idx in range(3):
        try:
            res = call_kimi_chat(messages, tools=tools, timeout=75)
            choice = res["choices"][0]
            msg = choice["message"]

            if choice.get("finish_reason") == "tool_calls" and msg.get("tool_calls"):
                log_msg(f"模型触发工具调用 (第 {loop_idx+1} 轮): 共 {len(msg['tool_calls'])} 个工具")
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_args = tc["function"].get("arguments", "{}")
                    if fn_name == "$web_search":
                        log_msg(f"触发内置联网搜索: {content}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": fn_name,
                            "content": fn_args,
                        })
                    else:
                        log_msg(f"执行 API 插件: {fn_name}({fn_args})")
                        if plugin_manager:
                            tool_res = plugin_manager.execute_tool(fn_name, fn_args)
                        else:
                            tool_res = {"error": "Plugin manager not loaded"}
                        res_str = json.dumps(tool_res, ensure_ascii=False) if isinstance(tool_res, (dict, list)) else str(tool_res)
                        log_msg(f"API 插件返回结果 ({fn_name}): {res_str[:120]}")
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": fn_name,
                            "content": res_str,
                        })
                continue

            content_text = msg.get("content", "").strip()
            if "$web_search" in content_text and loop_idx < 2:
                log_msg("检测到回答中包含原始 $web_search 标签，要求模型输出最终纯净回答...")
                messages.append(msg)
                messages.append({
                    "role": "user",
                    "content": "请根据搜索结果直接给出面向用户的最终回答，绝对不要输出任何 $web_search 或代码指令标签。",
                })
                continue

            final_reply = clean_bot_reply(content_text)
            break
        except Exception as e:
            log_msg(f"LLM API 调用异常 (第 {loop_idx+1} 轮): {e}")
            break

    return final_reply or clean_bot_reply(msg.get("content", "").strip())


def find_unread_badge_in_sidebar(img, w):
    """Scan the left conversation list for a red unread notification badge (R>215, G<85, B<85)."""
    sx1 = w["x"] + 62
    sy1 = w["y"] + 115
    sx2 = w["x"] + 145
    sy2 = w["y"] + w["height"] - 40
    sub = img.crop((sx1, sy1, sx2, sy2))
    px = sub.load()
    sw, sh = sub.size
    for y in range(0, sh, 2):
        for x in range(0, sw, 2):
            r, g, b = px[x, y]
            if r > 215 and g < 85 and b < 85:
                return sx1 + x + 40, sy1 + y + 10
    return None


def monitor_loop():
    prev_crop = None
    last_check_reply_ts = 0.0
    global last_recorded_text, last_bot_reply_ts, last_replied_user, last_reply_text, last_send_ts
    log_msg("监控循环已启动 (WeChat 4.1 + Kimi Vision + Web Search + Local Memory)")

    while True:
        try:
            ensure_processes()
            cfg = load_config()
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
                    # Auto-click unread red badge in sidebar if any
                    badge_pos = find_unread_badge_in_sidebar(img, w)
                    if badge_pos:
                        log_msg(f"检测到左侧会话列表红点未读消息，自动点击打开: {badge_pos}")
                        x11_click(badge_pos[0], badge_pos[1])
                        time.sleep(0.8)
                        img, wins = capture_screen_pil()

                    # Check if right panel has an open chat
                    if time.time() - last_send_ts > 4.5:
                        rx1 = w["x"] + int(w["width"] * 0.33)
                        ry1 = w["y"] + int(w["height"] * 0.38)
                        rx2 = w["x"] + int(w["width"] * 0.80)
                        ry2 = w["y"] + w["height"] - 140
                        cur_crop = img.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))

                        if prev_crop is not None:
                            diff = ImageChops.difference(cur_crop, prev_crop)
                            mean_diff = ImageStat.Stat(diff).mean[0]
                            if mean_diff > 1.2 and (time.time() - last_check_reply_ts > 3.5):
                                log_msg(f"检测到聊天窗口新动态 (diff={mean_diff:.2f})，分析最新消息...")
                                time.sleep(0.6)
                                img2, _ = capture_screen_pil()
                                chat_box = (
                                    w["x"] + int(w["width"] * 0.29),
                                    w["y"] + 8,
                                    w["x"] + w["width"] - 8,
                                    w["y"] + w["height"] - 125,
                                )
                                chat_img = img2.crop(chat_box)
                                prev_crop = img2.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                                last_check_reply_ts = time.time()

                                bot_names = cfg.get("bot_names", ["@"])
                                trigger_mode = cfg.get("trigger_mode", "mention_only")

                                try:
                                    session_active = (time.time() - last_bot_reply_ts < 30) and bool(last_replied_user)
                                    raw_msgs = parse_recent_messages_with_vision(
                                        chat_img, bot_names, last_replied_user=last_replied_user, session_active=session_active
                                    )

                                    reply_candidates = []

                                    for msg_item in raw_msgs:
                                        is_in = msg_item.get("is_incoming", True)
                                        s = msg_item.get("sender", "群友")
                                        c = msg_item.get("content", "").strip()
                                        is_m = msg_item.get("is_mentioned", False)
                                        is_f = msg_item.get("is_followup", False)
                                        q_info = msg_item.get("quote", {})
                                        img_info = msg_item.get("referenced_image", {})

                                        if not c:
                                            continue

                                        # Deduplicate against recent seen messages
                                        if is_recent_duplicate(s, c):
                                            continue
                                        mark_seen(s, c)

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

                                        # Always save every message to local DB memory for continuity
                                        save_msg(s, rec_c, is_bot=not is_in, is_mentioned=is_m)

                                        if not is_in:
                                            log_msg(f"提取自身发送消息: {c[:25]}，已记入本地历史")
                                            continue

                                        # 1. Stop command detection (e.g. "bot 别接话", "闭嘴")
                                        if is_stop_command(c):
                                            log_msg(f"检测到停用/闭嘴指令: '{c}'，立即清空连续追问会话")
                                            last_replied_user = ""
                                            last_bot_reply_ts = 0.0
                                            continue

                                        # 2. Trivial message / emoji reaction filter
                                        if is_trivial_or_reaction(c):
                                            log_msg(f"过滤非实质提问消息 (表情/标点/附和): '{c}'，不触发回复")
                                            continue

                                        # 3. Third-person mention correction (e.g. "不用加bot你也会回复")
                                        if is_m and not is_genuine_mention(c, bot_names):
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

                                        should_reply = (trigger_mode == "all") or is_m or valid_followup or valid_quote_reply
                                        log_msg(f"消息队列提取: sender={s}, content={c[:30]}, is_m={is_m}, valid_f={valid_followup}, valid_q={valid_quote_reply}, should_reply={should_reply}")

                                        if should_reply:
                                            reply_candidates.append({
                                                "sender": s,
                                                "content": c,
                                                "quote": q_info,
                                                "image": img_info,
                                            })

                                    # Pick at most ONE latest actionable incoming message from the batch
                                    if reply_candidates:
                                        target = reply_candidates[-1]
                                        s = target["sender"]
                                        c = target["content"]
                                        q_info = target["quote"]
                                        img_info = target["image"]

                                        log_msg(f"处理最新待回复消息 (共 {len(reply_candidates)} 条待回复，取最新一条): {s}: {c[:30]}")
                                        recent_history = get_recent(limit=15)
                                        reply_content = generate_reply_with_search(
                                            s,
                                            c,
                                            cfg,
                                            recent_history=recent_history,
                                            quote_info=q_info,
                                            image_info=img_info,
                                        )
                                        log_msg(f"生成回复: {reply_content}")
                                        if reply_content and reply_content != last_reply_text:
                                            x11_send_text(reply_content)
                                            save_msg("bot", reply_content, is_bot=True)
                                            mark_seen("bot", reply_content)
                                            last_recorded_text = reply_content
                                            last_reply_text = reply_content
                                            last_bot_reply_ts = time.time()
                                            last_replied_user = s
                                            log_msg(f"更新追问会话状态: 用户={last_replied_user}, 30秒追问窗口开启中")
                                            time.sleep(1.5)
                                            img3, _ = capture_screen_pil()
                                            prev_crop = img3.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                                            last_send_ts = time.time()
                                except Exception as ke:
                                    log_msg(f"消息处理异常: {ke}")
                        else:
                            prev_crop = cur_crop
                else:
                    prev_crop = None
        except Exception as e:
            log_msg(f"Monitor error: {e}")

        time.sleep(2.0)


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>WeChat + Kimi 群聊机器人控制台 (Oracle SG)</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 20px; }
  .wrap { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: 1fr 380px; gap: 20px; }
  .card { background: #1e293b; border-radius: 12px; padding: 16px; border: 1px solid #334155; }
  h2 { margin-top: 0; font-size: 18px; color: #38bdf8; }
  #screen-wrap { position: relative; display: inline-block; border: 2px solid #475569; border-radius: 8px; overflow: hidden; cursor: crosshair; background: #000; }
  #screen-img { display: block; max-width: 100%; height: auto; }
  input, textarea, select, button { width: 100%; box-sizing: border-box; margin-top: 6px; margin-bottom: 12px; padding: 9px 12px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #f8fafc; font-size: 14px; }
  button { background: #0284c7; border: none; font-weight: 600; cursor: pointer; }
  button:hover { background: #0369a1; }
  .log-box { background: #090d16; font-family: monospace; font-size: 12px; padding: 10px; height: 220px; overflow-y: auto; border-radius: 6px; border: 1px solid #334155; white-space: pre-wrap; }
  .badge { display: inline-block; padding: 3px 8px; border-radius: 99px; font-size: 12px; font-weight: bold; }
  .badge-ok { background: #065f46; color: #6ee7b7; }
  .badge-wait { background: #92400e; color: #fde68a; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h2>📱 微信实时画面（点击画面可直接在云端微信内操作） <span id="status-badge" class="badge badge-wait">检测中...</span></h2>
    <div id="screen-wrap">
      <img id="screen-img" src="/api/screen.png?t=0" onclick="handleScreenClick(event)">
    </div>
  </div>
  <div class="card">
    <h2>🤖 Kimi 机器人控制与配置</h2>
    <label>手动发送消息到当前打开的群聊：</label>
    <input type="text" id="manual-text" placeholder="输入要让小号在群里说的话...">
    <button onclick="sendManual()">🚀 立即发送到群聊</button>
    <hr style="border-color:#334155;margin:14px 0;">
    <label>自动回复开关：</label>
    <select id="auto-reply">
      <option value="true">开启 Kimi 自动回复</option>
      <option value="false">暂停自动回复（仅手动控制）</option>
    </select>
    <label>群聊触发模式：</label>
    <select id="trigger-mode">
      <option value="mention_only">仅被 @ 或呼叫小号名字时回复</option>
      <option value="all">群内所有新消息均回复</option>
    </select>
    <label>小号昵称 / 触发关键词（逗号分隔）：</label>
    <input type="text" id="bot-names" placeholder="@, bot">
    <label>高德地图 WebKey（可选，用于查美食与地铁换乘）：</label>
    <input type="text" id="amap-key" placeholder="高德开放平台申请的 Web服务 Key（留空则走全网搜索）">
    <label>Kimi 群聊人设 Prompt：</label>
    <textarea id="sys-prompt" rows="4"></textarea>
    <button onclick="saveCfg()">💾 保存配置</button>
    <h2>📜 实时运行日志</h2>
    <div id="logs" class="log-box"></div>
  </div>
</div>
<script>
async function refreshStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const badge = document.getElementById('status-badge');
    if (d.logged_in) {
      badge.className = 'badge badge-ok';
      badge.innerText = '✅ 已扫码登录在线';
    } else {
      badge.className = 'badge badge-wait';
      badge.innerText = '⏳ 等待手机微信扫码登录';
    }
    document.getElementById('logs').innerText = (d.logs || []).join('\n');
    const logEl = document.getElementById('logs');
    logEl.scrollTop = logEl.scrollHeight;
  } catch (e) {}
  document.getElementById('screen-img').src = '/api/screen.png?t=' + Date.now();
}
async function loadInitialCfg() {
  const r = await fetch('/api/status');
  const d = await r.json();
  if (d.config) {
    document.getElementById('auto-reply').value = String(d.config.auto_reply !== false);
    document.getElementById('trigger-mode').value = d.config.trigger_mode || 'mention_only';
    document.getElementById('bot-names').value = (d.config.bot_names || ['@']).join(', ');
    document.getElementById('amap-key').value = d.config.amap_api_key || '';
    document.getElementById('sys-prompt').value = d.config.system_prompt || '';
  }
}
async function handleScreenClick(ev) {
  const img = ev.target;
  const rect = img.getBoundingClientRect();
  const scaleX = 1280 / rect.width;
  const scaleY = 800 / rect.height;
  const x = Math.round((ev.clientX - rect.left) * scaleX);
  const y = Math.round((ev.clientY - rect.top) * scaleY);
  await fetch('/api/click', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({x, y})});
  setTimeout(refreshStatus, 400);
}
async function sendManual() {
  const t = document.getElementById('manual-text').value.trim();
  if (!t) return;
  await fetch('/api/send', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text: t})});
  document.getElementById('manual-text').value = '';
  setTimeout(refreshStatus, 500);
}
async function saveCfg() {
  const auto_reply = document.getElementById('auto-reply').value === 'true';
  const trigger_mode = document.getElementById('trigger-mode').value;
  const bot_names = document.getElementById('bot-names').value.split(',').map(s => s.trim()).filter(Boolean);
  const amap_api_key = document.getElementById('amap-key').value.trim();
  const system_prompt = document.getElementById('sys-prompt').value.trim();
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({auto_reply, trigger_mode, bot_names, amap_api_key, system_prompt})
  });
  alert('配置保存成功！');
  setTimeout(refreshStatus, 300);
}
loadInitialCfg();
refreshStatus();
setInterval(refreshStatus, 3000);
</script>
</body>
</html>
"""


class BotHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/screen.png":
            p = "/tmp/screen.png"
            if os.path.exists(p):
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
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
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
        elif parsed.path == "/api/status":
            _, wins = capture_screen_pil()
            logged_in = bool(wins and wins[0]["width"] >= 550)
            cfg = load_config()
            safe_cfg = {k: v for k, v in cfg.items() if k != "kimi_api_key"}
            with state_lock:
                logs_copy = list(recent_logs)
            self._send_json({
                "logged_in": logged_in,
                "windows": [{k: v for k, v in w.items() if k != "win"} for w in wins],
                "config": safe_cfg,
                "logs": logs_copy,
            })
        elif parsed.path == "/api/history":
            self._send_json({"history": get_recent(30)})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}

        if parsed.path == "/api/click":
            x = int(body.get("x", 640))
            y = int(body.get("y", 400))
            x11_click(x, y)
            log_msg(f"点击坐标 ({x}, {y})")
            self._send_json({"ok": True, "x": x, "y": y})
        elif parsed.path == "/api/send":
            text = body.get("text", "")
            ok, msg = x11_send_text(text)
            self._send_json({"ok": ok, "message": msg})
        elif parsed.path == "/api/send_image":
            img_path = body.get("path") or body.get("url") or ""
            ok, msg = x11_send_image(img_path)
            self._send_json({"ok": ok, "message": msg})
        elif parsed.path == "/api/config":
            cfg = load_config()
            for k in ("auto_reply", "trigger_mode", "bot_names", "system_prompt"):
                if k in body:
                    cfg[k] = body[k]
            save_config(cfg)
            log_msg(f"已更新配置: trigger_mode={cfg.get('trigger_mode')}, bot_names={cfg.get('bot_names')}")
            self._send_json({"ok": True})
        elif parsed.path == "/api/read_chat":
            img, wins = capture_screen_pil()
            if not wins:
                self._send_json({"ok": False, "error": "No WeChat window"})
                return
            w = wins[0]
            prompt = body.get("prompt", "请完整读取当前微信窗口中的群名、左侧会话列表以及右侧最新聊天记录内容，按条列出。")
            box = (w["x"], w["y"], w["x"] + w["width"], w["y"] + w["height"])
            buf = io.BytesIO()
            img.crop(box).save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }]
            res = call_kimi_chat(messages, timeout=35)
            summary = res["choices"][0]["message"]["content"].strip()
            self._send_json({"ok": True, "summary": summary})
        else:
            self.send_error(404)


if __name__ == "__main__":
    init_db()
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    server = ThreadingHTTPServer(("127.0.0.1", 18890), BotHTTPHandler)
    log_msg("HTTP 控制台已启动: http://127.0.0.1:18890")
    server.serve_forever()
