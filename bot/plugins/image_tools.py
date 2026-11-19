#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Image Tools Plugin: AI Image Generation, Photo Remixing (img2img) & Web Image Search
Supports:
1. generate_ai_image:
   - Text-to-Image (文生图) & Image-to-Image (垫图重制/改图/风格迁移) using OpenAI GPT-6.2 Astra (/v1/responses image_generation)
   - High-fidelity output, zero watermarks
   - Seamless fallback to Gemini (gemini-3-pro-image / gemini-2.5-flash-image) and Pollinations.ai if primary API unavailable
2. search_image: Live photo, wallpaper & meme search via Bing Async Image API
Images are downloaded and dispatched directly to WeChat chat via X11 clipboard.
"""

import urllib.request
import urllib.parse
import json
import re
import os
import random
import base64
import io
import time
import tempfile
from PIL import Image
from .base import BasePlugin, safe_http_get_bytes

Image.MAX_IMAGE_PIXELS = 36_000_000

# Global image sender hook registered by daemon
_IMAGE_SENDER = None

def register_image_sender(sender_fn):
    global _IMAGE_SENDER
    _IMAGE_SENDER = sender_fn


def get_image_sender():
    return _IMAGE_SENDER


# Global recent webpage cache for image extraction
_LAST_WEBPAGE_INFO = {
    "url": "",
    "title": "",
    "images": [],
    "ts": 0.0,
}

def set_last_webpage_info(url: str, title: str = "", images: list = None):
    global _LAST_WEBPAGE_INFO
    _LAST_WEBPAGE_INFO = {
        "url": url,
        "title": title,
        "images": images or [],
        "ts": time.time(),
    }

def get_last_webpage_info() -> dict:
    return _LAST_WEBPAGE_INFO


def extract_images_from_url(url: str, html_str: str = None) -> list:
    """从网页 HTML 中提取高质量真实内容配图与照片"""
    if not html_str:
        try:
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": url,
            }
            raw_html, _, _ = safe_http_get_bytes(url, headers=headers, timeout=10, max_bytes=4 * 1024 * 1024)
            html_str = raw_html.decode("utf-8", errors="ignore")
        except Exception:
            return []

    img_tags = re.findall(r'<img[^>]+>', html_str, re.IGNORECASE)
    results = []
    seen = set()

    bad_keywords = [
        "search-btn", "logo", "gototop", "ewm", "qrcode", "avatar", "icon",
        "button", "banner", ".svg", "blank.gif", "spacer.gif", "border"
    ]

    for tag in img_tags:
        src_m = re.search(r'(?:src|data-src|data-original|data-actualsrc)=[\'\"]([^\'\"]+)[\'\"]', tag, re.IGNORECASE)
        if not src_m:
            continue
        src = src_m.group(1).strip()
        if not src or src.startswith("data:image"):
            continue
        if any(b in src.lower() for b in bad_keywords):
            continue

        full_url = urllib.parse.urljoin(url, src)
        if full_url in seen:
            continue
        seen.add(full_url)

        alt_m = re.search(r'alt=[\'\"]([^\'\"]*)[\'\"]', tag, re.IGNORECASE)
        title_m = re.search(r'title=[\'\"]([^\'\"]*)[\'\"]', tag, re.IGNORECASE)
        desc = (alt_m.group(1) if alt_m else "") or (title_m.group(1) if title_m else "")

        results.append({"url": full_url, "desc": desc})

    return results


def _get_config():
    for p in ["/home/opc/wechat_bot/config.json", os.path.join(os.path.dirname(__file__), "..", "..", "config.json")]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}



def _resolve_openai_responses_url(cfg: dict) -> str:
    base_url = (cfg.get("openai_base_url") or "https://api.openai.com/v1/chat/completions").strip()
    if base_url.endswith("/responses"):
        return base_url
    if "/chat/completions" in base_url:
        return base_url.replace("/chat/completions", "/responses")
    return base_url.rstrip("/") + "/responses"


def _encode_ref_image_b64(ref_path: str, max_side: int = 1536) -> str:
    with Image.open(ref_path) as im:
        im = im.convert("RGB")
        if max(im.width, im.height) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _generate_via_openai_responses(
    openai_key: str,
    responses_url: str,
    model_name: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    ref_b64: str = "",
    timeout: int = 65,
):
    """Generate or remix an image using OpenAI Responses API (e.g. gpt-6.2-astra + image_generation tool)."""
    size_map = {
        "16:9": "1536x1024",
        "9:16": "1024x1536",
        "1:1": "1024x1024",
    }
    target_size = size_map.get(aspect_ratio, "1024x1024")
    if ref_b64:
        input_payload = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{ref_b64}"},
                ],
            }
        ]
    else:
        input_payload = prompt

    payload = {
        "model": model_name,
        "reasoning": {"effort": "low"},
        "input": input_payload,
        "tools": [
            {
                "type": "image_generation",
                "size": target_size,
                "quality": "medium",
            }
        ],
        "tool_choice": {"type": "image_generation"},
    }
    req = urllib.request.Request(
        responses_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
            "User-Agent": "WeChatBot/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode("utf-8"))

    for item in res.get("output", []):
        if item.get("type") == "image_generation_call" and item.get("result"):
            return base64.b64decode(item["result"])
    return None


def _generate_via_openai_images_api(
    openai_key: str,
    responses_url: str,
    model_name: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    timeout: int = 60,
):
    """Generate an image using OpenAI /v1/images/generations for dedicated gpt-image-* models."""
    size_map = {
        "16:9": "1536x1024",
        "9:16": "1024x1536",
        "1:1": "1024x1024",
    }
    images_url = responses_url.replace("/responses", "/images/generations")
    payload = {
        "model": model_name,
        "prompt": prompt,
        "size": size_map.get(aspect_ratio, "1024x1024"),
        "quality": "medium",
    }
    req = urllib.request.Request(
        images_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
            "User-Agent": "WeChatBot/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read().decode("utf-8"))

    data_items = res.get("data") or []
    if data_items and data_items[0].get("b64_json"):
        return base64.b64decode(data_items[0]["b64_json"])
    if data_items and data_items[0].get("url"):
        raw, _, _ = safe_http_get_bytes(data_items[0]["url"], headers={"User-Agent": "WeChatBot/2.0"}, timeout=20, max_bytes=16 * 1024 * 1024)
        return raw
    return None


class GenerateImagePlugin(BasePlugin):
    name = "generate_ai_image"
    description = "根据自然语言提示词使用 AI 绘画模型生成并直接向群聊发送高清图片。支持从零文本生图，以及以群聊中的照片/图片作为垫图进行【图片重制/改图/风格迁移/局部变换】"
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "详细生图或重制提示词（中英文皆可）。如果群友要求重制、改图、换人、换装，请详细写出要保留的构图和要修改的目标细节（例如：'重制这张照片，把里面的人换成薄肌黄毛，保持湖边草地背景'）",
            },
            "use_ref_image": {
                "type": "boolean",
                "default": False,
                "description": "是否以群聊中刚发送或引用的图片作为参考进行重制/垫图改图（图生图）。若群友说'重制照片'、'改一下'、'把图里的人变成'、'参考这张图'等，务必设为 true",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["1:1", "16:9", "9:16"],
                "default": "1:1",
                "description": "画面宽高比：'1:1'(方形头像/插画), '16:9'(横版电脑壁纸), '9:16'(竖版手机壁纸)",
            },
        },
        "required": ["prompt"],
    }

    fast_patterns = [
        # 仅针对明确独立的自然语言文生图走 Fast-Path 直通
        r'^(?:帮我)?(?:画一张|画一个|画一只|画张|画个|画只|画|生成一张|生成一个|生成图片|生成图|生成|来一张|来个|来只|画画|画图)\s*[:：]?\s*(?P<prompt>.+)$',
    ]

    def execute(self, prompt: str, aspect_ratio: str = "1:1", use_ref_image: bool = False, ref_image_path: str = None, **kwargs) -> dict:
        p = (prompt or "").strip()
        if not p:
            return {"error": "未提供生图提示词"}

        seed = random.randint(1000, 9999999)
        tmp_dir = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
        local_png = os.path.join(tmp_dir, f"wechat_ai_gen_{seed}.png")

        # Check if user intent is image remixing (图生图)
        is_remix = use_ref_image or any(k in p for k in ["重制", "改图", "修改", "换成", "变成", "参考", "仿照", "垫图", "照片里", "图里", "里面的人"])

        # Determine reference image (ONLY use clean cropped recent photos, NEVER whole UI screenshots or stale files!)
        ref_path = ref_image_path if (ref_image_path and os.path.exists(ref_image_path)) else None
        if not ref_path:
            now_ts = time.time()
            last_photo = "/tmp/wechat_last_photo.png"
            if os.path.exists(last_photo) and (now_ts - os.path.getmtime(last_photo) < 86400):
                ref_path = last_photo
            else:
                # Fallback: check newest persisted incoming image in data/images within last 6 hours
                img_dirs = [
                    "/home/opc/wechat_bot/data/images",
                    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "images"),
                ]
                for idir in img_dirs:
                    if os.path.isdir(idir):
                        try:
                            entries = [
                                os.path.join(idir, fn)
                                for fn in os.listdir(idir)
                                if fn.startswith("img_") and fn.endswith(".png")
                            ]
                            if entries:
                                newest = max(entries, key=lambda x: os.path.getmtime(x))
                                if now_ts - os.path.getmtime(newest) < 21600:
                                    ref_path = newest
                                    break
                        except Exception:
                            pass

        cfg = _get_config()
        openai_key = cfg.get("openai_api_key") or cfg.get("gpt_api_key") or os.environ.get("OPENAI_API_KEY", "")
        api_key = cfg.get("api_key") or cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
        img_model = (cfg.get("image_model") or "gpt-6.2-astra").strip()
        gen_success = False
        used_model = ""
        ref_b64 = ""

        if is_remix and ref_path and os.path.exists(ref_path):
            try:
                ref_b64 = _encode_ref_image_b64(ref_path)
            except Exception:
                ref_b64 = ""

        is_openai_primary = any(img_model.lower().startswith(p) for p in ("gpt-", "chatgpt-", "o1", "o3", "o4"))

        def _try_openai():
            nonlocal gen_success, used_model
            if gen_success or not openai_key:
                return
            responses_url = _resolve_openai_responses_url(cfg)
            target_openai_model = img_model if is_openai_primary else "gpt-6.2-astra"
            try:
                if target_openai_model.lower().startswith("gpt-image-") and not ref_b64:
                    raw = _generate_via_openai_images_api(
                        openai_key,
                        responses_url,
                        target_openai_model,
                        p,
                        aspect_ratio=aspect_ratio,
                        timeout=60,
                    )
                else:
                    # gpt-6.2-astra (and img2img remixes) run via /v1/responses + image_generation tool
                    resp_model = "gpt-6.2-astra" if target_openai_model.lower().startswith("gpt-image-") else target_openai_model
                    raw = _generate_via_openai_responses(
                        openai_key,
                        responses_url,
                        resp_model,
                        p,
                        aspect_ratio=aspect_ratio,
                        ref_b64=ref_b64,
                        timeout=65,
                    )
                if raw:
                    Image.open(io.BytesIO(raw)).convert("RGB").save(local_png, "PNG")
                    gen_success = True
                    used_model = target_openai_model
            except Exception:
                pass

        def _try_gemini():
            nonlocal gen_success, used_model
            if gen_success or not (api_key and (api_key.startswith("AQ.") or api_key.startswith("AIza"))):
                return
            models_to_try = []
            if not is_openai_primary and img_model:
                models_to_try.append(img_model)
            for fallback_m in ("gemini-3-pro-image", "gemini-2.5-flash-image"):
                if fallback_m not in models_to_try:
                    models_to_try.append(fallback_m)

            for target_model in models_to_try:
                if gen_success:
                    break
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
                    parts = [{"text": p}]
                    if ref_b64:
                        parts.append({"inlineData": {"mimeType": "image/png", "data": ref_b64}})

                    payload = {"contents": [{"parts": parts}]}
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=40) as r:
                        res = json.loads(r.read().decode("utf-8"))

                    cand = res.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for part in cand:
                        if "inlineData" in part:
                            raw = base64.b64decode(part["inlineData"]["data"])
                            Image.open(io.BytesIO(raw)).convert("RGB").save(local_png, "PNG")
                            gen_success = True
                            used_model = target_model
                            break
                except Exception:
                    continue

        # 1. Primary Engine: OpenAI GPT-6.2 Astra (default) -> Fallback: Gemini Image -> Pollinations
        if is_openai_primary:
            _try_openai()
            _try_gemini()
        else:
            _try_gemini()
            _try_openai()

        # 2. Fallback Engine: Pollinations.ai (Flux.1 / SDXL, free fallback)
        if not gen_success:
            if aspect_ratio == "16:9":
                w, h = 1024, 576
            elif aspect_ratio == "9:16":
                w, h = 576, 1024
            else:
                w, h = 768, 768

            enc_prompt = urllib.parse.quote(p)
            url = f"https://image.pollinations.ai/prompt/{enc_prompt}?width={w}&height={h}&nologo=true&seed={seed}"

            try:
                raw, _, _ = safe_http_get_bytes(url, headers={"User-Agent": "WeChatBot/2.0"}, timeout=25, max_bytes=12 * 1024 * 1024)
                Image.open(io.BytesIO(raw)).convert("RGB").save(local_png, "PNG")
                used_model = "pollinations"
            except Exception as e:
                return {"error": f"AI 绘画服务请求异常: {e}"}

        # Dispatch send if sender hook is registered
        sent_ok = False
        if _IMAGE_SENDER:
            res_s = _IMAGE_SENDER(local_png)
            sent_ok = res_s[0] if isinstance(res_s, tuple) else bool(res_s)

        return {
            "status": "success",
            "type": "image",
            "model": used_model,
            "image_sent_to_wechat": sent_ok,
            "prompt": p,
            "is_remix": is_remix,
            "image_path": local_png,
            "message": f"AI 已成功{'重制并' if is_remix else '绘制并'}发送图片：‘{p}’",
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"作图出了点问题：{result['error']}"
        action = "重制好了" if result.get("is_remix") else "画好了"
        return f"{action}，图发在上面了。"


class SearchImagePlugin(BasePlugin):
    name = "search_image"
    description = "在全网搜索指定人物的真实照片、新闻事件图片、风景壁纸或表情包并直接发送到微信群聊/私聊中"
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "搜索关键词，例如：'孔朝哲'、'故宫初雪'、'柴犬表情包'、'富士山高清壁纸'",
            },
            "image_type": {
                "type": "string",
                "enum": ["photo", "wallpaper", "meme"],
                "default": "photo",
                "description": "图片类型：'photo'(照片), 'wallpaper'(壁纸), 'meme'(表情包)",
            },
        },
        "required": ["keywords"],
    }

    fast_patterns = [
        r'^(?:帮我)?(?:找一张|找一个|找只|找张|找个|找图|搜一张|搜一个|搜只|搜张|搜个|搜图|搜索|看一张|看个)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|相片|壁纸|图片|图|表情包))?$',
        r'^(?:帮我)?(?:发一张|发一个|发只|发张|发图)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|相片|壁纸|图片|图|表情包))?$',
        r'^(?:帮我)?(?:发个|发)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|相片|壁纸|图片|表情包))$',
        r'^(?:帮我)?(?:搜|搜索|找|查)?(?P<keywords>.+?)的?(?P<type_hint>照片|照片儿|相片|模样|长相)(?:发一下|发出来|发个|发发|发群里)?$',
        r'^(?:看下|看看|瞧瞧)(?P<keywords>.+?)的?(?P<type_hint>照片|相片|图)$',
    ]

    def execute(self, keywords: str, image_type: str = "photo", type_hint: str = None, **kwargs) -> dict:
        kw = (keywords or "").strip()
        if not kw:
            return {"error": "未提供搜图关键词"}

        # Resolve type_hint from fast path regex if provided
        if type_hint:
            if "表情包" in type_hint:
                image_type = "meme"
            elif "壁纸" in type_hint:
                image_type = "wallpaper"

        search_kw = kw
        if (image_type == "wallpaper" or "壁纸" in kw) and "壁纸" not in search_kw:
            search_kw += " 高清壁纸"
        elif (image_type == "meme" or "表情包" in kw) and "表情包" not in search_kw:
            search_kw += " 表情包"

        seed = random.randint(1000, 9999999)
        local_png = f"/tmp/wechat_search_{seed}.png"
        headers_dl = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }

        # 1. 优先使用 Google Serper Images API（海量真实人物与全网高清图片，不被墙不被拦截）
        try:
            from .serper_pool import get_serper_keys, discard_exhausted_serper_key
            serper_keys = get_serper_keys()
            for k in list(serper_keys):
                try:
                    payload = json.dumps({"q": search_kw, "gl": "cn", "hl": "zh-cn"}).encode("utf-8")
                    req = urllib.request.Request(
                        "https://google.serper.dev/images",
                        data=payload,
                        headers={"X-API-KEY": k, "Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=10) as r:
                        res_data = json.loads(r.read().decode("utf-8"))

                    img_list = res_data.get("images", [])
                    if img_list:
                        for img_item in img_list[:6]:
                            img_url = img_item.get("imageUrl")
                            if not img_url:
                                continue
                            try:
                                data, _, _ = safe_http_get_bytes(img_url, headers=headers_dl, timeout=8, max_bytes=10 * 1024 * 1024)
                                if len(data) >= 5000:
                                    im = Image.open(io.BytesIO(data))
                                    if im.width >= 100 and im.height >= 100:
                                        im.convert("RGB").save(local_png, "PNG")
                                        sent_ok = False
                                        if _IMAGE_SENDER:
                                            res_s = _IMAGE_SENDER(local_png)
                                            sent_ok = res_s[0] if isinstance(res_s, tuple) else bool(res_s)
                                        return {
                                            "status": "success",
                                            "type": "image",
                                            "image_sent_to_wechat": sent_ok,
                                            "keywords": kw,
                                            "title": img_item.get("title", ""),
                                            "source": img_item.get("link", ""),
                                            "image_path": local_png,
                                            "message": f"已成功检索并发送‘{kw}’的图片",
                                        }
                            except Exception:
                                continue
                    break  # Serper search succeeded, don't try other keys
                except urllib.error.HTTPError as he:
                    if he.code == 403:
                        discard_exhausted_serper_key(k, "403 Forbidden")
                        continue
                except Exception:
                    pass
        except Exception:
            pass

        # 2. 备用兜底：Bing Async Images API
        try:
            enc_kw = urllib.parse.quote(search_kw)
            url = f"https://www.bing.com/images/async?q={enc_kw}&first=1&count=8"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", errors="ignore")

            murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', html)
            if murls:
                for target_url in murls[:4]:
                    try:
                        data, _, _ = safe_http_get_bytes(target_url, headers=headers_dl, timeout=8, max_bytes=10 * 1024 * 1024)
                        if len(data) >= 5000:
                            im = Image.open(io.BytesIO(data))
                            if im.width >= 100 and im.height >= 100:
                                im.convert("RGB").save(local_png, "PNG")
                                sent_ok = False
                                if _IMAGE_SENDER:
                                    res_s = _IMAGE_SENDER(local_png)
                                    sent_ok = res_s[0] if isinstance(res_s, tuple) else bool(res_s)
                                return {
                                    "status": "success",
                                    "type": "image",
                                    "image_sent_to_wechat": sent_ok,
                                    "keywords": kw,
                                    "image_path": local_png,
                                    "message": f"已成功检索并发送‘{kw}’的图片",
                                }
                    except Exception:
                        continue
        except Exception as e:
            pass

        return {"error": f"未能检索或下载到与‘{kw}’相关的有效图片"}

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"搜图出了点问题：{result['error']}"
        title = result.get("title")
        hint = f"（{title}）" if title and len(title) < 25 else ""
        return f"图发在上面了{hint}，瞅瞅是不是这张。"


class SendWebpageImagePlugin(BasePlugin):
    name = "send_webpage_image"
    description = "从指定的网页链接（或上下文刚刚提到的文章/网页链接）中读取、提取真实正文照片、合影或配图，并直接发送到微信窗口"
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "目标网页链接（如 http://... 或 https://...）。若未提供，系统会自动读取当前聊天上下文中最新分享或解析的网页链接",
            },
            "keyword": {
                "type": "string",
                "description": "匹配特定图片的关键词（例如：'合影'、'孔朝哲'、'证书'、'图'等），留空则默认选取网页正文中主要的第一张高清大图",
            },
            "image_index": {
                "type": "integer",
                "default": 1,
                "description": "选择第几张图片（从 1 开始计数，默认 1）",
            },
        },
    }

    fast_patterns = [
        r'^(?:你就?不能)?(?:把|将)?(?:网页(?:里|中)?的?)?(?:照片|图片|图|合影)(?:给?我)?(?:发出来|发一下|发个|发到群里|发我|发发|发下)(?:吗|吧|呀)?$',
        r'^(?:你就?不能)?(?:发出来|发一下|发张|发个|发发)(?:网页(?:里|中)?的?)?(?:照片|图片|图|合影)(?:吗|吧|呀)?$',
        r'^(?:把|将)?(?:这篇?|这个?)?(?:网页|文章|链接)(?:里|中)?的?(?:照片|图片|图|合影)(?:发出来|发一下|发群里|发我)(?:吗|吧|呀)?$',
        r'^(?:网页(?:里|中)?的?)?(?:照片|图片|图|合影)(?:发出来|发一下|发我|发下)(?:吗|吧|呀)?$',
    ]

    def execute(self, url: str = None, keyword: str = "", image_index: int = 1, **kwargs) -> dict:
        target_url = (url or "").strip()
        last_info = get_last_webpage_info()
        if not target_url:
            target_url = last_info.get("url", "")
            if not target_url:
                return {"error": "未提供网页链接，且最近未分享过网页，请提供网页 URL"}

        candidate_items = []
        if target_url == last_info.get("url") and last_info.get("images"):
            candidate_items = [{"url": u, "desc": ""} for u in last_info.get("images", [])]

        if not candidate_items:
            candidate_items = extract_images_from_url(target_url)

        if not candidate_items:
            return {"error": f"未能从网页【{target_url}】提取到有效照片或图片"}

        # If keyword provided, match alt/title/url
        matched_candidates = []
        kw = (keyword or "").strip().lower()
        if kw:
            for item in candidate_items:
                if kw in item.get("desc", "").lower() or kw in item.get("url", "").lower():
                    matched_candidates.append(item)

        final_candidates = matched_candidates if matched_candidates else candidate_items

        idx = max(0, min(image_index - 1, len(final_candidates) - 1))
        chosen_item = final_candidates[idx]

        seed = random.randint(1000, 9999999)
        local_raw = f"/tmp/wechat_wp_{seed}_raw"
        local_png = f"/tmp/wechat_wp_{seed}.png"

        downloaded = False
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": target_url,
        }

        to_try = [chosen_item] + [c for c in final_candidates if c != chosen_item][:4]
        for item in to_try:
            u = item["url"]
            try:
                data, _, _ = safe_http_get_bytes(u, headers=headers, timeout=10, max_bytes=10 * 1024 * 1024)
                if len(data) >= 5000:
                    with open(local_raw, "wb") as f:
                        f.write(data)
                    im = Image.open(local_raw)
                    if im.width >= 100 and im.height >= 100:
                        im.convert("RGB").save(local_png, "PNG")
                        downloaded = True
                        chosen_url = u
                        break
            except Exception:
                continue
            finally:
                if os.path.exists(local_raw):
                    try:
                        os.remove(local_raw)
                    except Exception:
                        pass

        if not downloaded:
            return {"error": "网页图片下载失败或源地址拒绝访问"}

        sent_ok = False
        if _IMAGE_SENDER:
            res_s = _IMAGE_SENDER(local_png)
            sent_ok = res_s[0] if isinstance(res_s, tuple) else bool(res_s)

        return {
            "status": "success",
            "type": "webpage_image",
            "image_sent_to_wechat": sent_ok,
            "url": target_url,
            "image_url": chosen_url,
            "image_path": local_png,
            "message": "已成功从网页中提取并发送照片到聊天窗口",
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"发图出了点问题：{result['error']}"
        return "网页里的照片发在上面了，瞅瞅是不是这张。"
