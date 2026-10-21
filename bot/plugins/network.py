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
from .serper_pool import get_serper_keys, discard_exhausted_serper_key


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
        import os
        import urllib.parse
        import html as html_lib
        q = (query or "").strip()
        if not q:
            return {"error": "未提供搜索关键词"}

        items = []
        # 1. Primary: Serper.dev Google Search API Key Pool (Covers Domestic & Global platforms with real-time Google index)
        # Automatic key failover: when a key runs out of credits (403), auto-discard and try next key seamlessly!
        serper_keys = get_serper_keys()
        for serper_key in serper_keys:
            try:
                s_req = urllib.request.Request(
                    "https://google.serper.dev/search",
                    data=json.dumps({"q": q, "gl": "cn", "hl": "zh-cn", "num": 6}).encode("utf-8"),
                    headers={
                        "X-API-KEY": serper_key,
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(s_req, timeout=7) as r:
                    s_data = json.loads(r.read().decode("utf-8", errors="ignore"))

                # Check for quota/credit error in JSON body
                if isinstance(s_data, dict):
                    err_msg = str(s_data.get("message") or "")
                    sc = s_data.get("statusCode")
                    if sc in (401, 403, 429) or any(w in err_msg.lower() for w in ["unauthorized", "not enough credits", "credit", "quota"]):
                        masked = f"{serper_key[:6]}...{serper_key[-4:]}" if len(serper_key) >= 10 else serper_key
                        discard_exhausted_serper_key(serper_key, reason=err_msg or "Credits exhausted")
                        continue

                ab = s_data.get("answerBox")
                if isinstance(ab, dict):
                    ab_snip = ab.get("answer") or ab.get("snippet") or ""
                    if ab_snip:
                        items.append({
                            "title": ab.get("title", "Google 精选摘要"),
                            "snippet": str(ab_snip),
                            "url": ab.get("link", ""),
                        })

                kg = s_data.get("knowledgeGraph")
                if isinstance(kg, dict) and kg.get("description"):
                    items.append({
                        "title": f"{kg.get('title', q)} ({kg.get('type', '知识图谱')})",
                        "snippet": str(kg.get("description", "")),
                        "url": kg.get("descriptionLink", ""),
                    })

                for org in s_data.get("organic", [])[:6]:
                    title = (org.get("title") or "").strip()
                    snippet = (org.get("snippet") or "").strip()
                    date_str = (org.get("date") or "").strip()
                    if title and snippet:
                        items.append({
                            "title": title,
                            "snippet": (f"[{date_str}] {snippet}" if date_str else snippet),
                            "url": org.get("link", ""),
                        })

                if items:
                    break
            except urllib.error.HTTPError as he:
                masked = f"{serper_key[:6]}...{serper_key[-4:]}" if len(serper_key) >= 10 else serper_key
                if he.code in (401, 403, 429) or "credit" in str(he).lower():
                    discard_exhausted_serper_key(serper_key, reason=f"HTTP {he.code}: {he.reason}")
                    continue
                else:
                    continue
            except Exception as e:
                err_s = str(e).lower()
                if any(w in err_s for w in ["401", "403", "unauthorized", "credit"]):
                    discard_exhausted_serper_key(serper_key, reason=str(e))
                    continue

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        q_tokens = [tok.lower() for tok in re.split(r'[\s,，。？！?!"\']+', q) if len(tok) >= 2]
        if not q_tokens and q:
            q_tokens = [q.lower()]

        def is_relevant(title_str: str, snippet_str: str) -> bool:
            combined = (title_str + " " + snippet_str).lower()
            return any(tok in combined for tok in q_tokens)

        # 2. Fallback: Chinese Wikipedia API (if Serper returned no results)
        if not items:
            try:
                wiki_url = f"https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q)}&utf8=1&format=json"
                w_req = urllib.request.Request(wiki_url, headers={"User-Agent": "WeChatBot/1.0 (https://github.com/minglu-lin/wechat-vision-bot)"})
                with urllib.request.urlopen(w_req, timeout=5) as r:
                    w_data = json.loads(r.read().decode("utf-8", errors="ignore"))
                for entry in w_data.get("query", {}).get("search", [])[:4]:
                    c_title = html_lib.unescape(re.sub(r'<[^>]+>', '', entry.get("title", "")).strip())
                    c_snip = html_lib.unescape(re.sub(r'<[^>]+>', '', entry.get("snippet", "")).strip())
                    if c_title and c_snip and is_relevant(c_title, c_snip):
                        items.append({
                            "title": f"{c_title} - 维基百科",
                            "snippet": c_snip,
                            "url": f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(c_title)}",
                        })
            except Exception:
                pass

        # 2. Secondary: Bing search with strict relevance filter (blocks fake decoy results like 淘宝/小红书/Google翻译)
        if len(items) < 3:
            try:
                url = f"https://www.bing.com/search?q={urllib.parse.quote(q)}&ensearch=0"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as r:
                    html = r.read().decode("utf-8", errors="ignore")

                for block in re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html, re.DOTALL)[:6]:
                    t_m = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
                    p_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
                    if t_m and p_m:
                        href = html_lib.unescape(t_m.group(1))
                        clean_title = html_lib.unescape(re.sub(r'<[^>]+>', '', t_m.group(2)).strip())
                        clean_snippet = html_lib.unescape(re.sub(r'<[^>]+>', '', p_m.group(1)).strip())
                        if clean_title and clean_snippet and is_relevant(clean_title, clean_snippet):
                            items.append({
                                "title": clean_title,
                                "snippet": clean_snippet,
                                "url": href,
                            })
            except Exception:
                pass

        return {
            "query": q,
            "count": len(items),
            "results": items if items else [{
                "snippet": f"已检索关键词‘{q}’，未发现更多外部网页增量信息。请直接结合你自身掌握的历史与百科知识直接给出最终回答，严禁再次重复调用 web_search 工具！"
            }]
        }

