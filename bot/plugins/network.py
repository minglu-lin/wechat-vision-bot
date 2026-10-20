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
