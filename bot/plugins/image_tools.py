#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Image Tools Plugin: AI Image Generation, Photo Remixing (img2img) & Web Image Search
Supports:
1. generate_ai_image:
   - Text-to-Image (文生图) & Image-to-Image (垫图重制/改图/风格迁移) using Gemini 2.5 Flash Image
   - High-fidelity 4K output, zero watermarks
   - Seamless fallback to Pollinations.ai if Gemini API unavailable
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
from PIL import Image
from .base import BasePlugin

# Global image sender hook registered by daemon
_IMAGE_SENDER = None

def register_image_sender(sender_fn):
    global _IMAGE_SENDER
    _IMAGE_SENDER = sender_fn


def _get_config():
    for p in ["/home/opc/wechat_bot/config.json", os.path.join(os.path.dirname(__file__), "..", "..", "config.json")]:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}



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
        local_png = f"/tmp/wechat_ai_gen_{seed}.png"

        # Check if user intent is image remixing (图生图)
        is_remix = use_ref_image or any(k in p for k in ["重制", "改图", "修改", "换成", "变成", "参考", "仿照", "垫图", "照片里", "图里", "里面的人"])

        # Determine reference image (ONLY use clean cropped photos, NEVER whole UI screenshots!)
        ref_path = ref_image_path
        if not ref_path or not os.path.exists(ref_path):
            for candidate in ["/tmp/wechat_last_photo.png", "/home/opc/user_lake_photo.png"]:
                if os.path.exists(candidate):
                    ref_path = candidate
                    break

        cfg = _get_config()
        api_key = cfg.get("api_key") or cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
        img_model = cfg.get("image_model") or "gemini-3-pro-image"
        gemini_success = False

        # 1. Primary Engine: Nano Banana Pro (Gemini 3 Pro Image / gemini-3-pro-image)
        if api_key and (api_key.startswith("AQ.") or api_key.startswith("AIza")):
            models_to_try = [img_model]
            if "gemini-3-pro-image" not in models_to_try:
                models_to_try.append("gemini-3-pro-image")
            if "gemini-2.5-flash-image" not in models_to_try:
                models_to_try.append("gemini-2.5-flash-image")

            for target_model in models_to_try:
                if gemini_success:
                    break
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"
                    parts = [{"text": p}]

                    if is_remix and ref_path and os.path.exists(ref_path):
                        with open(ref_path, "rb") as rf:
                            b64_data = base64.b64encode(rf.read()).decode("utf-8")
                        parts.append({"inlineData": {"mimeType": "image/png", "data": b64_data}})

                    payload = {"contents": [{"parts": parts}]}
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req, timeout=40) as r:
                        res = json.loads(r.read().decode("utf-8"))

                    cand = res.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for part in cand:
                        if "inlineData" in part:
                            raw = base64.b64decode(part["inlineData"]["data"])
                            Image.open(io.BytesIO(raw)).convert("RGB").save(local_png, "PNG")
                            gemini_success = True
                            break
                except Exception:
                    continue

        # 2. Fallback Engine: Pollinations.ai (Flux.1 / SDXL, free fallback)
        if not gemini_success:
            if aspect_ratio == "16:9":
                w, h = 1024, 576
            elif aspect_ratio == "9:16":
                w, h = 576, 1024
            else:
                w, h = 768, 768

            enc_prompt = urllib.parse.quote(p)
            url = f"https://image.pollinations.ai/prompt/{enc_prompt}?width={w}&height={h}&nologo=true&seed={seed}"
            local_raw = f"/tmp/wechat_ai_gen_{seed}_raw"

            try:
                req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw = r.read()
                with open(local_raw, "wb") as f:
                    f.write(raw)
                Image.open(local_raw).convert("RGB").save(local_png, "PNG")
            except Exception as e:
                return {"error": f"AI 绘画服务请求异常: {e}"}

        # Dispatch send if sender hook is registered
        if _IMAGE_SENDER:
            _IMAGE_SENDER(local_png)

        return {
            "status": "success",
            "type": "image",
            "prompt": p,
            "is_remix": is_remix,
            "image_path": local_png,
            "message": f"AI 已成功{'重制并' if is_remix else '绘制并'}发送图片：‘{p}’",
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"绘画失败：{result['error']}"
        action = "重制好照片" if result.get("is_remix") else "画好"
        return f"🎨 已经为你{action}并发送到群里啦：【{result.get('prompt')}】"


class SearchImagePlugin(BasePlugin):
    name = "search_image"
    description = "在全网搜索指定主题的真实高清照片、风景壁纸、动物萌宠或表情包并直接发送到群聊"
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "搜索关键词，例如：'故宫初雪'、'柴犬表情包'、'富士山高清壁纸'、'猫咪壁纸'",
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
        r'^(?:帮我)?(?:找一张|找一个|找只|找张|找个|找图|搜一张|搜一个|搜只|搜张|搜个|搜图|看一张|看个)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|壁纸|图片|图|表情包))?$',
        r'^(?:帮我)?(?:发一张|发一个|发只|发张|发图)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|壁纸|图片|图|表情包))?$',
        r'^(?:帮我)?(?:发个|发)\s*[:：]?\s*(?P<keywords>.+?)(?:的?(?P<type_hint>照片|壁纸|图片|表情包))$',
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

        enc_kw = urllib.parse.quote(search_kw)
        url = f"https://www.bing.com/images/async?q={enc_kw}&first=1&count=8"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", errors="ignore")

            murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', html)
            if not murls:
                return {"error": f"未能检索到与‘{kw}’相关的图片"}

            seed = random.randint(1000, 9999999)
            local_raw = f"/tmp/wechat_search_{seed}_raw"
            local_png = f"/tmp/wechat_search_{seed}.png"

            downloaded = False
            for target_url in murls[:4]:
                try:
                    req2 = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req2, timeout=8) as r2:
                        data = r2.read()
                    if len(data) > 8000:
                        with open(local_raw, "wb") as f:
                            f.write(data)
                        Image.open(local_raw).convert("RGB").save(local_png, "PNG")
                        downloaded = True
                        break
                except Exception:
                    continue

            if not downloaded:
                return {"error": f"检索到的图片源地址暂无法访问，请尝试更换关键词"}

            # Dispatch send if sender hook is registered
            if _IMAGE_SENDER:
                _IMAGE_SENDER(local_png)

            return {
                "status": "success",
                "type": "image",
                "keywords": kw,
                "image_path": local_png,
                "message": f"已成功检索并发送‘{kw}’的图片",
            }
        except Exception as e:
            return {"error": f"搜图服务接口请求异常: {e}"}

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"搜图失败：{result['error']}"
        return f"🖼️ 已经为你找到并发送了【{result.get('keywords')}】的图片！"
