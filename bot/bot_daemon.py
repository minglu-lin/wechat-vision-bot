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
from Xlib import X, XK, display
from Xlib.ext import xtest

os.environ["DISPLAY"] = ":99"
BASE_DIR = "/home/opc/wechat_bot"
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


def call_kimi_chat(messages, tools=None, timeout=35):
    cfg = load_config()
    api_key = cfg.get("kimi_api_key", "")
    base_url = cfg.get("kimi_base_url", "https://api.kimi.com/coding/v1/chat/completions")
    model = cfg.get("kimi_model", "kimi-for-coding-highspeed")

    payload = {
        "model": model,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "KimiCLI/0.77",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_latest_message_with_vision(chat_img, bot_names, last_replied_user="", session_active=False):
    buf = io.BytesIO()
    chat_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    session_instruction = ""
    if session_active and last_replied_user:
        session_instruction = (
            f"6. 【连续追问会话判断（重要）】：bot 刚在 90 秒内回答了群友【{last_replied_user}】。\n"
            f"   - 如果最底部消息是【{last_replied_user}】发送的，判断它是否是在对 bot 的回答进行继续追问、要求重查、评价或多轮对话（例如‘那你再查一下’、‘为什么’、‘然后呢’、‘真的吗’、或者省略主语的追问）？\n"
            f"   - 如果是【{last_replied_user}】的连续追问，请将 is_followup 设为 true；如果是跟群里其他人闲聊或与 bot 无关，则为 false。\n"
        )

    prompt = (
        f"这是一张微信群聊窗口最新截图。\n"
        f"请重点观察聊天记录区域最底部最新的一条消息（以及紧密关联的上下文/引用/图片）：\n\n"
        f"1. 【气泡方向】：最底部最新的一条消息是左侧白色气泡（群友发的，is_incoming=true）还是右侧绿色气泡（我们自己刚发的，is_incoming=false）？\n"
        f"2. 【发送者与正文】：发送者的群昵称是什么？最新消息的主体文本内容是什么？\n"
        f"3. 【呼叫触发】：该消息是否呼叫了以下名字之一：{bot_names}，或者包含'@'？（包含则 is_mentioned=true）\n"
        f"4. 【微信引用识别】：该消息是否使用了微信的‘引用’功能（即气泡内或下方带有灰色边框/分割线，显示引用的某人发言或图片）？\n"
        f"   - 如果有引用：请在 quote 字段中记录被引用的发送者(quoted_sender)及被引用的具体文字或图片描述(quoted_content)。\n"
        f"5. 【关联图片/截图OCR识别（重要）】：\n"
        f"   - 检查最新消息本身是否是一张图片，或者最新消息紧邻的上一条消息是否是群友刚刚发送的图片/截图，或者消息是否引用了图片，或者提问语义是否指向图片（如‘看图’、‘为什么搜到’等）？\n"
        f"   - 如果存在相关图片/截图：请尽可能详细地提取并描述该图片！包括：图片类型（如谷歌搜索结果截图/报错截图/图表/照片）、图片中所有可见文字/标题/搜索关键词（OCR提取）、搜索结果或画面展示的核心信息。\n"
        f"{session_instruction}\n"
        f"请严格输出纯 JSON，不要带 markdown 代码块（不要带 ```json）：\n"
        f'{{\n'
        f'  "is_incoming": true,\n'
        f'  "sender": "昵称",\n'
        f'  "content": "最新消息正文",\n'
        f'  "is_mentioned": true,\n'
        f'  "is_followup": false,\n'
        f'  "quote": {{\n'
        f'    "has_quote": false,\n'
        f'    "quoted_sender": "",\n'
        f'    "quoted_content": ""\n'
        f'  }},\n'
        f'  "referenced_image": {{\n'
        f'    "has_image": false,\n'
        f'    "image_type": "",\n'
        f'    "ocr_and_detail": ""\n'
        f'  }}\n'
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
    res = call_kimi_chat(messages, timeout=50)
    text = res["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        if "{" in text and "}" in text:
            json_str = text[text.find("{"):text.rfind("}")+1]
            return json.loads(json_str)
        return json.loads(text)
    except Exception as je:
        log_msg(f"Vision JSON 解析兜底: {je}")
        is_incoming = "true" in text.lower() and '"is_incoming": true' in text.lower()
        is_mentioned = "true" in text.lower() and '"is_mentioned": true' in text.lower()
        return {
            "is_incoming": is_incoming,
            "sender": "群友",
            "content": text,
            "is_mentioned": is_mentioned,
            "is_followup": False,
            "quote": {"has_quote": False, "quoted_sender": "", "quoted_content": ""},
            "referenced_image": {"has_image": False, "image_type": "", "ocr_and_detail": ""},
        }


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
    sys_prompt = cfg.get("system_prompt", "")
    tools = [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    now_str = datetime.now(TZ_BJ).strftime("%Y年%m月%d日 %H:%M")

    history_str = ""
    if recent_history:
        lines = [f"[{h['time']}] {h['sender']}: {h['content']}" for h in recent_history]
        history_str = "【群聊最近上下文记录（供参考理解语境、代词与话题连贯性）】：\n" + "\n".join(lines) + "\n\n"

    system_instruction = (
        f"{sys_prompt}\n"
        f"【当前系统时间】：{now_str}（北京时间）。\n"
        f"{history_str}"
        f"【时效性与事实搜索指引】：如果对方询问了需要时效性、实时事实、当天股市/行情、最新新闻、具体人物或资料查询等，请务必使用联网搜索工具获取最新真实数据。\n"
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
        f"3. 保持沉稳、自然、客观的真实朋友口吻，不用太俏皮或刻意调侃，严禁客服腔和AI味。\n"
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

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_query},
    ]

    final_reply = ""
    for loop_idx in range(3):
        try:
            res = call_kimi_chat(messages, tools=tools, timeout=35)
            choice = res["choices"][0]
            msg = choice["message"]

            if choice.get("finish_reason") == "tool_calls" and msg.get("tool_calls"):
                log_msg(f"Kimi 触发联网搜索 (第 {loop_idx+1} 轮): {content}")
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["function"]["name"],
                        "content": tc["function"]["arguments"],
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
            log_msg(f"Kimi API 调用异常 (第 {loop_idx+1} 轮): {e}")
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
    global last_recorded_text
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
                                    session_active = (time.time() - last_bot_reply_ts < 90) and bool(last_replied_user)
                                    parsed = parse_latest_message_with_vision(
                                        chat_img, bot_names, last_replied_user=last_replied_user, session_active=session_active
                                    )
                                    is_in = parsed.get("is_incoming")
                                    s = parsed.get("sender", "群友")
                                    c = parsed.get("content", "").strip()
                                    is_m = parsed.get("is_mentioned")
                                    is_f = parsed.get("is_followup", False)
                                    q_info = parsed.get("quote", {})
                                    img_info = parsed.get("referenced_image", {})

                                    quotes_bot = False
                                    if q_info and q_info.get("has_quote"):
                                        q_s = q_info.get("quoted_sender", "")
                                        if any(bn.lower() in str(q_s).lower() for bn in ["bot", "albot", "aibot", "微信小号"]) or q_s == "":
                                            quotes_bot = True

                                    is_valid_followup = (session_active and s == last_replied_user and is_f) or quotes_bot
                                    should_reply = (trigger_mode == "all") or is_m or is_valid_followup

                                    has_q = q_info.get("has_quote", False) if isinstance(q_info, dict) else False
                                    has_img = img_info.get("has_image", False) if isinstance(img_info, dict) else False
                                    log_msg(f"消息解析: is_incoming={is_in}, sender={s}, content={c}, is_mentioned={is_m}, is_followup={is_f}, should_reply={should_reply}")

                                    rec_c = c
                                    if has_q and q_info.get("quoted_content"):
                                        rec_c = f"[引用 {q_info.get('quoted_sender')}: {str(q_info.get('quoted_content'))[:35]}] " + rec_c
                                    if has_img and img_info.get("image_type"):
                                        rec_c = f"[附图: {img_info.get('image_type')}] " + rec_c

                                    if rec_c and rec_c != last_recorded_text:
                                        save_msg(s, rec_c, is_bot=not is_in, is_mentioned=is_m)
                                        last_recorded_text = rec_c

                                    if is_in:
                                        if should_reply:
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
                                                last_recorded_text = reply_content
                                                last_reply_text = reply_content
                                                last_bot_reply_ts = time.time()
                                                last_replied_user = s
                                                log_msg(f"更新追问会话状态: 用户={last_replied_user}, 90秒追问窗口开启中")
                                                time.sleep(1.0)
                                                img3, _ = capture_screen_pil()
                                                prev_crop = img3.crop((rx1, ry1, rx2, ry2)).convert("L").resize((160, 120))
                                        else:
                                            log_msg("判定结果: 未被呼叫且非追问，已记入本地历史，跳过回复")
                                    else:
                                        log_msg("判定结果: 自身发送消息，已记入本地历史，跳过回复")
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
  const system_prompt = document.getElementById('sys-prompt').value.trim();
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({auto_reply, trigger_mode, bot_names, system_prompt})
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
