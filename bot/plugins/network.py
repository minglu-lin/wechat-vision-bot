#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Network & Web Intelligence Plugins:
1. lookup_ip: IPv4/Domain geolocation & ASN lookup (ip-api.com + ipwho.is HTTPS fallback)
2. web_search: Multi-query + automatic bilingual global Google Search via Serper key pool
3. read_webpage: SSRF-safe full webpage & HTML table reader for deep verification
"""

from concurrent.futures import ThreadPoolExecutor
import html as html_lib
import json
import re
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from .base import BasePlugin, UnsafeURLError, safe_http_get_bytes
from .serper_pool import discard_exhausted_serper_key, get_serper_keys


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
        if not re.fullmatch(r'[a-zA-Z0-9.\-:]{1,253}', target):
            return {"error": "无效的 IP 地址或域名格式"}

        enc_target = urllib.parse.quote(target, safe=":")
        url = f"http://ip-api.com/json/{enc_target}?lang=zh-CN"
        data = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            data = None

        if not isinstance(data, dict) or data.get("status") != "success":
            # HTTPS fallback via ipwho.is
            try:
                fb_url = f"https://ipwho.is/{enc_target}?lang=zh-CN"
                req2 = urllib.request.Request(fb_url, headers={"User-Agent": "WeChatBot/2.0"})
                with urllib.request.urlopen(req2, timeout=6) as r2:
                    d2 = json.loads(r2.read().decode("utf-8"))
                if isinstance(d2, dict) and d2.get("success") is not False and d2.get("ip"):
                    conn_info = d2.get("connection") or {}
                    parts = [p for p in [d2.get("country", ""), d2.get("region", ""), d2.get("city", "")] if p]
                    return {
                        "ip": d2.get("ip", target),
                        "location": " · ".join(parts) if parts else "未知位置",
                        "country": d2.get("country", ""),
                        "region": d2.get("region", ""),
                        "city": d2.get("city", ""),
                        "isp": conn_info.get("isp", ""),
                        "org": conn_info.get("org", ""),
                        "asn": f"AS{conn_info.get('asn')}" if conn_info.get("asn") else "",
                    }
            except Exception as e:
                return {"error": f"IP 归属地接口请求超时或失败: {e}"}
            return {"error": f"查询 IP 失败: {(data or {}).get('message', '未知错误')}"}

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
            "🌐 【IP 归属地查询】",
            f"• 目标 IP：{ip}",
            f"• 物理归属：{loc}",
            f"• 运营商/机构：{isp}" + (f" ({org})" if org and org != isp else ""),
        ]
        if asn:
            lines.append(f"• 网络 ASN：{asn}")

        return "\n".join(lines)


class WebSearchPlugin(BasePlugin):
    name = "web_search"
    description = (
        "全网 Google 联网搜索工具：当需要查询实时事实、最新发布的模型/产品参数与官方定价、当天新闻、股票异动原因或未知资料时调用。"
        "支持单条 query 或并发多条 queries（建议对科技/AI/海外资讯同时传入英文一手关键词，如 ['GPT-6.2 Astra API pricing per million tokens', 'GPT-6.2 Astra 官方定价']）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "主搜索关键词或查询短语（科技/海外主题优先用英文关键词查一手官网数据）",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选：并发执行的多组互补搜索词（最多 3 条，例如中英文双语并发检索）",
            },
        },
        "required": [],
    }

    _CN_TO_EN_TECH_MAP = [
        (r'价格对比|定价对比', 'API pricing comparison per million tokens'),
        (r'价格|定价|收费|多少钱|单价|费用', 'API pricing cost per million tokens'),
        (r'对比|区别|差距|哪个好', 'vs comparison'),
        (r'跑分|基准测试|评测', 'benchmarks evaluation'),
        (r'发布|上线|推出|更新|初体验', 'release announcement'),
        (r'参数|规格|上下文', 'specs context window'),
        (r'盘前|异动|暴涨|暴跌', 'stock premarket news catalyst'),
        (r'官网|官方文档', 'official documentation'),
    ]

    @classmethod
    def normalize_search_query(cls, raw_q: str) -> str:
        """Insert spaces between glued numbers/English/CJK tokens (e.g. '6.2astra和6.1astra' -> '6.2 astra 和 6.1 astra')."""
        s = (raw_q or "").strip()
        # Separate version numbers glued directly to English words: 6.2astra -> 6.2 astra
        s = re.sub(r'(\d+\.\d+)([A-Za-z]{2,})', r'\1 \2', s)
        return re.sub(r'\s+', ' ', s).strip()

    @classmethod
    def build_auto_global_query(cls, q: str) -> Optional[str]:
        """When a Chinese query asks about an English tech/AI/ticker entity, build a companion English query for global index."""
        has_cjk = bool(re.search(r'[\u4e00-\u9fa5]', q))
        if not has_cjk:
            return None
        en_tokens = re.findall(r'[A-Za-z][A-Za-z0-9\.\-_]*|\d+\.\d+', q)
        if not en_tokens or not any(re.search(r'[A-Za-z]{2,}', t) for t in en_tokens):
            return None

        work = cls.normalize_search_query(q)
        matched_en_intents: List[str] = []
        for pat, en_rep in cls._CN_TO_EN_TECH_MAP:
            if re.search(pat, work):
                matched_en_intents.append(en_rep)
        work_en = re.sub(r'(?:和|与|跟|对比|对标)', ' vs ', work)
        work_en = re.sub(r'[\u4e00-\u9fa5，。？！、：；“”‘’（）]+', ' ', work_en)
        work_en = re.sub(r'\s+', ' ', work_en).strip()
        if not work_en:
            return None
        if matched_en_intents:
            extra = " ".join(dict.fromkeys(matched_en_intents[:2]))
            return f"{work_en} {extra}".strip()
        return f"{work_en} official news specs".strip()

    @staticmethod
    def _serper_single_fetch(q_str: str, is_global_en: bool = False) -> List[Dict[str, str]]:
        out_items: List[Dict[str, str]] = []
        serper_keys = get_serper_keys()
        payload_dict: Dict[str, Any] = {"q": q_str, "num": 6}
        if is_global_en:
            payload_dict["gl"] = "us"
            payload_dict["hl"] = "en"
        else:
            payload_dict["gl"] = "cn"
            payload_dict["hl"] = "zh-cn"

        for serper_key in serper_keys:
            try:
                s_req = urllib.request.Request(
                    "https://google.serper.dev/search",
                    data=json.dumps(payload_dict).encode("utf-8"),
                    headers={
                        "X-API-KEY": serper_key,
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(s_req, timeout=7) as r:
                    s_data = json.loads(r.read().decode("utf-8", errors="ignore"))

                if isinstance(s_data, dict):
                    err_msg = str(s_data.get("message") or "")
                    sc = s_data.get("statusCode")
                    if sc in (401, 403, 429) or any(w in err_msg.lower() for w in ["unauthorized", "not enough credits", "credit", "quota"]):
                        discard_exhausted_serper_key(serper_key, reason=err_msg or "Credits exhausted")
                        continue

                ab = s_data.get("answerBox")
                if isinstance(ab, dict):
                    ab_snip = ab.get("answer") or ab.get("snippet") or ""
                    if ab_snip:
                        out_items.append({
                            "title": ab.get("title", "Google Featured Answer"),
                            "snippet": str(ab_snip),
                            "url": ab.get("link", ""),
                        })

                kg = s_data.get("knowledgeGraph")
                if isinstance(kg, dict) and kg.get("description"):
                    attrs = kg.get("attributes")
                    attr_str = ""
                    if isinstance(attrs, dict) and attrs:
                        attr_str = " | " + "; ".join(f"{k}: {v}" for k, v in list(attrs.items())[:6])
                    out_items.append({
                        "title": f"{kg.get('title', q_str)} ({kg.get('type', 'Knowledge Graph')})",
                        "snippet": str(kg.get("description", "")) + attr_str,
                        "url": kg.get("descriptionLink", ""),
                    })

                for org in s_data.get("organic", [])[:6]:
                    title = (org.get("title") or "").strip()
                    snippet = (org.get("snippet") or "").strip()
                    date_str = (org.get("date") or "").strip()
                    sitelinks = org.get("sitelinks") or []
                    sl_extra = ""
                    if isinstance(sitelinks, list) and sitelinks:
                        sl_titles = [str(s.get("title", "")).strip() for s in sitelinks[:3] if isinstance(s, dict) and s.get("title")]
                        if sl_titles:
                            sl_extra = " [子栏目: " + " / ".join(sl_titles) + "]"
                    if title and snippet:
                        out_items.append({
                            "title": title,
                            "snippet": (f"[{date_str}] {snippet}{sl_extra}" if date_str else f"{snippet}{sl_extra}"),
                            "url": org.get("link", ""),
                        })

                if out_items:
                    break
            except urllib.error.HTTPError as he:
                if he.code in (401, 403, 429) or "credit" in str(he).lower():
                    discard_exhausted_serper_key(serper_key, reason=f"HTTP {he.code}: {he.reason}")
                    continue
            except Exception as e:
                if any(w in str(e).lower() for w in ["401", "403", "unauthorized", "credit"]):
                    discard_exhausted_serper_key(serper_key, reason=str(e))
                    continue
        return out_items

    def execute(self, query: str = "", queries: Optional[List[str]] = None, **kwargs) -> dict:
        q_list: List[str] = []
        if isinstance(queries, list):
            for item in queries:
                if item and str(item).strip():
                    q_list.append(self.normalize_search_query(str(item)))
        if query and str(query).strip():
            nq = self.normalize_search_query(str(query))
            if nq not in q_list:
                q_list.insert(0, nq)

        q_list = [x for x in q_list if x][:3]
        if not q_list:
            return {"error": "未提供搜索关键词"}

        # Build execution plan: [(query_str, is_global_en)]
        plan: List[tuple] = []
        for q_item in q_list:
            is_pure_ascii = not bool(re.search(r'[\u4e00-\u9fa5]', q_item))
            plan.append((q_item, is_pure_ascii))

        # Auto bilingual global boost when only 1 mixed Chinese+English tech query was given
        if len(plan) == 1 and not plan[0][1]:
            en_boost = self.build_auto_global_query(plan[0][0])
            if en_boost and en_boost.lower() != plan[0][0].lower():
                plan.append((en_boost, True))

        if len(plan) == 1:
            batch_results = [self._serper_single_fetch(plan[0][0], plan[0][1])]
        else:
            with ThreadPoolExecutor(max_workers=min(3, len(plan))) as pool:
                batch_results = list(pool.map(lambda args: self._serper_single_fetch(args[0], args[1]), plan))

        # Interleave results across queries (so both official English docs and Chinese context appear at top)
        items: List[Dict[str, str]] = []
        seen_urls = set()
        max_len = max((len(r) for r in batch_results), default=0)
        for idx in range(max_len):
            for r_list in batch_results:
                if idx < len(r_list):
                    it = r_list[idx]
                    u_key = (it.get("url") or it.get("title") or "").strip().lower()
                    if u_key and u_key not in seen_urls:
                        seen_urls.add(u_key)
                        items.append(it)
                        if len(items) >= 10:
                            break
            if len(items) >= 10:
                break

        primary_q = q_list[0]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        q_tokens = [tok.lower() for tok in re.split(r'[\s,，。？！?!"\']+', primary_q) if len(tok) >= 2]
        if not q_tokens and primary_q:
            q_tokens = [primary_q.lower()]

        def is_relevant(title_str: str, snippet_str: str) -> bool:
            combined = (title_str + " " + snippet_str).lower()
            return any(tok in combined for tok in q_tokens)

        # Fallback 1: Wikipedia API (if Serper returned no results)
        if not items:
            try:
                wiki_url = f"https://zh.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(primary_q)}&utf8=1&format=json"
                w_req = urllib.request.Request(wiki_url, headers={"User-Agent": "WeChatBot/2.0"})
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

        # Fallback 2: Bing search
        if len(items) < 2:
            try:
                url = f"https://www.bing.com/search?q={urllib.parse.quote(primary_q)}&ensearch=0"
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
            "query": " | ".join(q for q, _ in plan),
            "count": len(items),
            "results": items if items else [{
                "snippet": f"已检索关键词‘{primary_q}’，未发现更多外部网页增量信息。请结合已有权威知识直接给出回答，或改换更精准的英文实体词检索。"
            }],
        }


class ReadWebpagePlugin(BasePlugin):
    name = "read_webpage"
    description = (
        "抓取并读取指定公开网页 URL 的完整正文与数据表格（例如从 web_search 返回的官方文档链接、定价页面、新闻原文中读取未被摘要截断的完整参数表与价格表）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要读取的 http/https 网页链接（如 https://developers.openai.com/api/docs/pricing）",
            },
            "focus_keyword": {
                "type": "string",
                "description": "可选：重点定位关键词（如 'gpt-6.2-astra' 或 'pricing'），优先截取包含该词的正文与表格段落",
            },
        },
        "required": ["url"],
    }

    @staticmethod
    def html_to_structured_text(html_str: str) -> tuple:
        title_m = (
            re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html_str, re.I)
            or re.search(r'<title[^>]*>(.*?)</title>', html_str, re.I | re.S)
        )
        title = html_lib.unescape(re.sub(r'\s+', ' ', title_m.group(1)).strip()) if title_m else ""

        # Strip scripts, styles, svg, nav, footer
        cleaned = re.sub(r'<(script|style|noscript|svg|iframe)[^>]*>[\s\S]*?</\1>', ' ', html_str, flags=re.I)

        # Preserve table cells with pipe separators and rows with newlines
        cleaned = re.sub(r'</t[dh]\s*>', ' | ', cleaned, flags=re.I)
        cleaned = re.sub(r'</tr\s*>', '\n', cleaned, flags=re.I)
        cleaned = re.sub(r'<(?:br|hr)\s*/?>', '\n', cleaned, flags=re.I)
        cleaned = re.sub(r'</(?:p|div|li|h[1-6]|section|article|blockquote)\s*>', '\n', cleaned, flags=re.I)
        cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
        cleaned = html_lib.unescape(cleaned)

        lines = []
        for raw_ln in cleaned.splitlines():
            ln = re.sub(r'[ \t\u00a0]+', ' ', raw_ln).strip(" |")
            if len(ln) >= 2:
                lines.append(ln)
        return title, "\n".join(lines)

    def execute(self, url: str = "", focus_keyword: str = "", **kwargs) -> dict:
        u = (url or "").strip()
        if not u:
            return {"error": "未提供 URL"}
        try:
            raw, ctype, final_u = safe_http_get_bytes(
                u,
                headers={
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
                },
                timeout=9,
                max_bytes=3 * 1024 * 1024,
            )
        except UnsafeURLError as se:
            return {"error": f"安全策略拦截该 URL: {se}"}
        except Exception as e:
            return {"error": f"读取网页失败: {e}"}

        charset = "utf-8"
        if "charset=" in (ctype or "").lower():
            charset = ctype.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            html_str = raw.decode(charset, errors="replace")
        except Exception:
            html_str = raw.decode("utf-8", errors="replace")

        title, full_text = self.html_to_structured_text(html_str)
        if not full_text:
            return {"error": "网页正文为空或由纯前端动态渲染"}

        fk = (focus_keyword or "").strip().lower()
        max_chars = 4800
        if len(full_text) > max_chars and fk:
            idx = full_text.lower().find(fk)
            if idx >= 0:
                start = max(0, idx - 900)
                end = min(len(full_text), start + max_chars)
                excerpt = full_text[start:end]
                return {
                    "url": final_u,
                    "title": title,
                    "matched_keyword": focus_keyword,
                    "content": excerpt,
                }

        return {
            "url": final_u,
            "title": title,
            "content": full_text[:max_chars],
        }

