#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Network Plugin: IP Geolocation & Domain Lookup
Provides IP address geographic location, ISP, and ASN information via ip-api.com.
"""

import urllib.request
import json
import re
from .base import BasePlugin


class NetworkPlugin(BasePlugin):
    name = "lookup_ip"
    description = "查询指定 IP 地址或域名的地理位置归属地（国家、省份、城市）、互联网服务提供商 (ISP) 与网络组织"
    parameters = {
        "type": "object",
        "properties": {
            "ip": {
                "type": "string",
                "description": "需要查询的 IPv4 地址或域名，例如：8.8.8.8、114.114.114.114",
            }
        },
        "required": ["ip"],
    }

    fast_patterns = [
        r'^(?:查一下?|查下?|查询?|查|看下?|看一看)?\s*(?:ip|IP|归属地)\s*(?P<ip>[a-zA-Z0-9.\-_:]+)$',
        r'^(?:查一下?|查下?|查询?|查|看下?|看一看)?\s*(?P<ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*(?:ip|IP|归属地|位置|是哪里的?|在哪)?$',
    ]

    def execute(self, ip: str, **kwargs) -> dict:
        target = (ip or "").strip()
        if not target:
            return {"error": "未提供要查询的 IP 地址"}

        url = f"http://ip-api.com/json/{target}?lang=zh-CN"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"IP 归属地接口请求超时或失败: {e}"}

        if data.get("status") != "success":
            return {"error": f"查询 IP 失败: {data.get('message', '未知错误')}"}

        country = data.get("country", "")
        region = data.get("regionName", "")
        city = data.get("city", "")
        isp = data.get("isp", "")
        org = data.get("org", "")
        asn = data.get("as", "")
        query_ip = data.get("query", target)

        location_parts = [p for p in [country, region, city] if p]
        location_str = " · ".join(location_parts) if location_parts else "未知位置"

        return {
            "ip": query_ip,
            "location": location_str,
            "country": country,
            "region": region,
            "city": city,
            "isp": isp,
            "org": org,
            "asn": asn,
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询 IP 失败：{result['error']}"

        ip = result["ip"]
        loc = result["location"]
        isp = result.get("isp", "未知")
        org = result.get("org", "")
        asn = result.get("asn", "")

        lines = [
            f"🌐 【IP 归属地查询】",
            f"• 目标 IP：{ip}",
            f"• 物理归属：{loc}",
            f"• 运营商/机构：{isp}" + (f" ({org})" if org and org != isp else ""),
        ]
        if asn:
            lines.append(f"• 网络 ASN：{asn}")

        return "\n".join(lines)


class WebSearchPlugin(BasePlugin):
    name = "web_search"
    description = "全网联网搜索工具：当需要了解实时事实、最新新闻、当天行情、具体资料细节或查询未知事物时调用，检索全网最新真实报道"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词或查询短语",
            }
        },
        "required": ["query"],
    }

    def execute(self, query: str, **kwargs) -> dict:
        import urllib.parse
        q = (query or "").strip()
        if not q:
            return {"error": "未提供搜索关键词"}

        url = f"https://cn.bing.com/search?q={urllib.parse.quote(q)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as r:
                html = r.read().decode("utf-8", errors="ignore")

            items = []
            matches = re.findall(r'<li class="b_algo">.*?<h2><a\s+href="([^"]+)".*?>(.*?)</a></h2>.*?<p>(.*?)</p>', html, re.DOTALL)
            for href, raw_title, raw_snippet in matches[:5]:
                clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
                clean_snippet = re.sub(r'<[^>]+>', '', raw_snippet).strip()
                if clean_title and clean_snippet:
                    items.append({
                        "title": clean_title,
                        "snippet": clean_snippet,
                        "url": href,
                    })

            if not items:
                snippets = re.findall(r'<p class="b_lineclamp\d*">(.*?)</p>', html)
                for s in snippets[:4]:
                    cs = re.sub(r'<[^>]+>', '', s).strip()
                    if cs:
                        items.append({"title": q, "snippet": cs, "url": ""})

            return {
                "query": q,
                "count": len(items),
                "results": items if items else [{"snippet": f"已联网检索关键词‘{q}’，未发现明显异动网页。"}]
            }
        except Exception as e:
            return {"error": f"联网搜索请求失败: {e}"}

