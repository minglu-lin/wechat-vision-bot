#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Map & Local Life Plugin: Food & Restaurant Search & Subway/Transit Routing
Integrated with Amap (高德地图开放平台) Web Service API.
Supports:
1. POI Food & Dining Search (restaurant names, ratings, average cost, addresses, phone numbers)
2. Public Transit & Subway Route Planning (line names, transfer stations, stops, duration, ticket price)
Falls back gracefully to Web Search when no key is configured.
"""

import urllib.request
import urllib.parse
import json
import re
import os
from .base import BasePlugin

def _get_amap_key() -> str:
    """Read Amap key from environment or config.json."""
    env_key = os.environ.get("AMAP_API_KEY", "").strip()
    if env_key:
        return env_key

    # Check config.json paths
    base_dirs = [
        "/home/opc/wechat_bot",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.getcwd(),
    ]
    for b in base_dirs:
        cfg_file = os.path.join(b, "config.json")
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    k = data.get("amap_api_key", "").strip()
                    if k:
                        return k
            except Exception:
                pass
    return ""


class FoodSearchPlugin(BasePlugin):
    name = "search_local_food"
    description = "查询指定城市/商圈的餐饮美食、特色餐厅、大众点评高分口碑店、人均消费预算、详细地址与推荐招牌菜"
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "美食或餐厅关键词，例如：'北京烤鸭'、'铜锅涮肉'、'炸酱面'、'日料'、'火锅'、'四季民福'、'聚宝源'",
            },
            "city": {
                "type": "string",
                "description": "所在城市名称，默认为'北京'",
                "default": "北京",
            },
            "area": {
                "type": "string",
                "description": "特定商圈、区县或地标（可选），例如：'王府井'、'三里屯'、'朝阳大悦城'、'西单'、'望京'",
            },
        },
        "required": ["keywords"],
    }

    fast_patterns = [
        r'^(?:推荐|查一下?|看下?)?(?:北京|上海|广州|深圳|杭州|成都)?(?:有什么|附近有啥|好吃的)?(?P<keywords>[\u4e00-\u9fa5]{2,10}(?:烤鸭|火锅|涮肉|炸酱面|美食|烤肉|日料|拉面|小吃|甜品|早茶|西餐))(?:\s*推荐)?$',
    ]

    def match_fast_path(self, text: str):
        if not _get_amap_key():
            return None
        return super().match_fast_path(text)

    def execute(self, keywords: str, city: str = "北京", area: str = "", **kwargs) -> dict:
        kw = (keywords or "").strip()
        c = (city or "北京").strip()
        a = (area or "").strip()
        if not kw:
            return {"error": "未提供美食关键词"}

        amap_key = _get_amap_key()
        search_query = f"{a} {kw}".strip() if a else kw

        if not amap_key:
            return {
                "status": "need_key_or_llm_search",
                "query": search_query,
                "city": c,
                "message": (
                    "未检测到高德地图 API Key (amap_api_key)。"
                    "请使用大模型结合全网大众点评/小红书美食探店口碑与真实常识，为用户推荐 3~5 家该地区最地道、口碑最好的高分餐厅，"
                    "包含：餐厅名、人均消费、招牌必点菜品、所在商圈与特色推荐理由。"
                )
            }

        # Query Amap POI Text API
        enc_kw = urllib.parse.quote(search_query)
        enc_city = urllib.parse.quote(c)
        url = (
            f"https://restapi.amap.com/v3/place/text?key={amap_key}"
            f"&keywords={enc_kw}&city={enc_city}&types=050000&offset=6&page=1&extensions=all"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"高德餐饮数据接口请求失败: {e}"}

        if data.get("status") != "1":
            return {"error": f"查询美食商户失败: {data.get('info', '未知错误')}"}

        pois = data.get("pois", [])
        if not pois:
            return {"error": f"在{c}{a}未能检索到与‘{kw}’相关的餐厅商户"}

        results = []
        for p in pois:
            biz = p.get("biz_ext", {}) or {}
            rating = biz.get("rating")
            cost = biz.get("cost")
            results.append({
                "name": p.get("name", ""),
                "rating": f"{rating}分" if rating and str(rating) != "[]" else "暂无评分",
                "cost": f"人均约 ¥{cost}" if cost and str(cost) != "[]" else "人均消费不详",
                "type": p.get("type", "").split(";")[-1] if p.get("type") else "特色餐饮",
                "address": p.get("address", "详见门店"),
                "tel": p.get("tel", "暂无电话"),
            })

        return {
            "city": c,
            "query": search_query,
            "total_found": len(results),
            "restaurants": results,
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询美食出错：{result['error']}"

        if result.get("status") == "need_key_or_llm_search":
            return f"🍴 正在为您检索【{result['city']} {result['query']}】的口碑餐厅推荐..."

        lines = [
            f"🍴 【{result['city']} 美食餐厅推荐】 (搜索: {result['query']})",
        ]
        for idx, r in enumerate(result.get("restaurants", []), 1):
            lines.append(
                f"{idx}. {r['name']}（{r['rating']} | {r['cost']}）\n"
                f"   • 类型：{r['type']}\n"
                f"   • 地址：{r['address']}"
            )
        return "\n".join(lines)


class LocalPlaceSearchPlugin(BasePlugin):
    name = "search_local_places"
    description = (
        "查询城市/商圈的各类线下实体生活场所与商户推荐（包含商场购物、男装/女装买衣服、攀岩馆/健身房/体育运动场馆、"
        "酒吧/夜生活/休闲娱乐、文化场馆/博物馆/公园景区、生活服务等），返回真实商户名、高德/口碑评分、人均消费预算、详细地址与分类"
    )
    parameters = {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "场所或品类关键词，例如：'攀岩馆'、'男装 买衣服'、'购物中心'、'健身房'、'羽毛球馆'、'酒吧'、'书店'、'Livehouse'、'滑冰场'",
            },
            "city": {
                "type": "string",
                "description": "所在城市名称，默认为'北京'",
                "default": "北京",
            },
            "area": {
                "type": "string",
                "description": "特定商圈、区县或地标（可选），例如：'海淀'、'中关村'、'五道口'、'三里屯'、'朝阳'、'西单'、'望京'",
            },
            "category": {
                "type": "string",
                "description": "场所分类过滤（可选）：'shopping'(购物/商场/服装), 'sports'(运动健身/攀岩/球馆), 'entertainment'(酒吧/娱乐), 'culture'(博物馆/文化), 'scenic'(景区公园), 'all'(不限)",
                "enum": ["all", "shopping", "sports", "entertainment", "culture", "scenic"],
                "default": "all",
            },
        },
        "required": ["keywords"],
    }

    CATEGORY_MAP = {
        "shopping": "060000",
        "sports": "080000",
        "entertainment": "080300",
        "culture": "140000",
        "scenic": "110000",
        "dining": "050000",
    }

    fast_patterns = [
        r'^(?:推荐|查一下?|看下?)?(?P<area>北京|海淀|朝阳|西城|东城|丰台|昌平|中关村|五道口|三里屯|西单|望京)?(?:有什么|附近有啥|在哪买|哪里的)?(?P<keywords>[\u4e00-\u9fa5]{2,12}(?:攀岩馆|攀岩|健身房|羽毛球馆|游泳馆|台球|男装|买衣服|服装店|商场|购物中心|酒吧|Livehouse|书店|博物馆))(?:\s*推荐)?$',
    ]

    def match_fast_path(self, text: str):
        if not _get_amap_key():
            return None
        return super().match_fast_path(text)

    def execute(self, keywords: str, city: str = "北京", area: str = "", category: str = "all", **kwargs) -> dict:
        kw = (keywords or "").strip()
        c = (city or "北京").strip()
        a = (area or "").strip()
        if not kw:
            return {"error": "未提供场所关键词"}

        amap_key = _get_amap_key()
        search_query = f"{a} {kw}".strip() if a else kw

        if not amap_key:
            return {
                "status": "need_key_or_llm_search",
                "query": search_query,
                "city": c,
                "message": (
                    "未检测到高德地图 API Key (amap_api_key)。"
                    "请使用大模型结合全网高德地图与大众点评/小红书真实口碑常识，为用户推荐 3~5 家该地区口碑最好、真实存在的线下商户/场馆，"
                    "包含：店名/场馆名、参考评分、人均预算、具体商圈/地址与特色亮点。"
                )
            }

        enc_kw = urllib.parse.quote(search_query)
        enc_city = urllib.parse.quote(c)
        type_code = self.CATEGORY_MAP.get(category.lower(), "") if category != "all" else ""
        type_param = f"&types={type_code}" if type_code else ""

        url = (
            f"https://restapi.amap.com/v3/place/text?key={amap_key}"
            f"&keywords={enc_kw}&city={enc_city}{type_param}&offset=8&page=1&extensions=all"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"高德地点数据接口请求失败: {e}"}

        if data.get("status") != "1":
            return {"error": f"查询商户地点失败: {data.get('info', '未知错误')}"}

        pois = data.get("pois", [])
        if not pois and type_param:
            # Fallback without type restriction if 0 results
            url_fallback = (
                f"https://restapi.amap.com/v3/place/text?key={amap_key}"
                f"&keywords={enc_kw}&city={enc_city}&offset=8&page=1&extensions=all"
            )
            try:
                req = urllib.request.Request(url_fallback, headers={"User-Agent": "WeChatBot/2.0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8"))
                pois = data.get("pois", [])
            except Exception:
                pass

        if not pois:
            return {"error": f"在{c}{a}未能检索到与‘{kw}’相关的商户或场馆"}

        results = []
        for p in pois:
            biz = p.get("biz_ext", {}) or {}
            rating = biz.get("rating")
            cost = biz.get("cost")
            results.append({
                "name": p.get("name", ""),
                "rating": f"{rating}分" if rating and str(rating) != "[]" else "暂无评分",
                "cost": f"人均约 ¥{cost}" if cost and str(cost) != "[]" else "消费视项目而定",
                "type": p.get("type", "").split(";")[-1] if p.get("type") else "商户/场馆",
                "address": p.get("address", "详见门店"),
                "tel": p.get("tel", "暂无电话"),
            })

        return {
            "city": c,
            "query": search_query,
            "total_found": len(results),
            "places": results,
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询地点出错：{result['error']}"

        if result.get("status") == "need_key_or_llm_search":
            return f"📍 正在为您检索【{result['city']} {result['query']}】的优质地点与口碑场馆..."

        lines = [
            f"📍 【{result['city']} 地点与口碑商户推荐】 (检索: {result['query']})",
        ]
        for idx, r in enumerate(result.get("places", []), 1):
            lines.append(
                f"{idx}. {r['name']}（{r['rating']} | {r['cost']}）\n"
                f"   • 分类：{r['type']}\n"
                f"   • 地址：{r['address']}"
            )
        return "\n".join(lines)


class TransitRoutePlugin(BasePlugin):
    name = "get_transit_route"
    description = "查询城市内的公共交通与地铁换乘路线规划方案（包含乘坐哪条地铁线、换乘站点、途经站数、预计总耗时、步行距离与票价）"
    parameters = {
        "type": "object",
        "properties": {
            "origin": {
                "type": "string",
                "description": "出发地点或地铁站，例如：'北京南站'、'望京'、'西单'、'天安门广场'",
            },
            "destination": {
                "type": "string",
                "description": "目的地或地铁站，例如：'颐和园'、'北京大兴国际机场'、'三里屯'、'中关村'",
            },
            "city": {
                "type": "string",
                "description": "所在城市名称，默认为'北京'",
                "default": "北京",
            },
        },
        "required": ["origin", "destination"],
    }

    fast_patterns = [
        r'^(?:从)?(?P<origin>[\u4e00-\u9fa50-9]{2,10}?)\s*(?:坐地铁到|坐公交到|怎么坐车到|到|去)\s*(?P<destination>[\u4e00-\u9fa50-9]{2,10}?)(?:\s*(?:怎么坐车|怎么走|怎么坐地铁|地铁路线|路线|地铁))?$',
    ]

    def match_fast_path(self, text: str):
        if not _get_amap_key():
            return None
        return super().match_fast_path(text)

    def _geocode(self, address: str, city: str, key: str) -> tuple:
        enc_addr = urllib.parse.quote(address)
        enc_city = urllib.parse.quote(city)
        url = f"https://restapi.amap.com/v3/geocode/geo?key={key}&address={enc_addr}&city={enc_city}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
            geocodes = data.get("geocodes", [])
            if geocodes:
                loc = geocodes[0].get("location", "")
                if "," in loc:
                    return loc.split(",", 1)
        except Exception:
            pass
        return None, None

    def execute(self, origin: str, destination: str, city: str = "北京", **kwargs) -> dict:
        orig = (origin or "").strip()
        dest = (destination or "").strip()
        c = (city or "北京").strip()
        if not orig or not dest:
            return {"error": "未提供起点或终点地点"}

        amap_key = _get_amap_key()
        if not amap_key:
            return {
                "status": "need_key_or_llm_search",
                "origin": orig,
                "destination": dest,
                "city": c,
                "message": (
                    "未配置高德地图 API Key (amap_api_key)。"
                    "请使用大模型结合该城市真实的地铁网络与地理交通知识，为用户规划出最合理的公共交通/地铁换乘路线，"
                    "详细说明：从哪一站进站、乘坐几号线、在哪个中转站换乘、换乘几号线、到哪一站下车出站，以及大致预计用时和票价。"
                )
            }

        # 1. Geocode origin & destination
        o_lon, o_lat = self._geocode(orig, c, amap_key)
        d_lon, d_lat = self._geocode(dest, c, amap_key)
        if not o_lon or not d_lon:
            return {"error": f"未能解析‘{orig}’或‘{dest}’的地理坐标"}

        # 2. Transit integrated routing
        enc_city = urllib.parse.quote(c)
        url = (
            f"https://restapi.amap.com/v3/direction/transit/integrated?key={amap_key}"
            f"&origin={o_lon},{o_lat}&destination={d_lon},{d_lat}&city={enc_city}&strategy=0"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"高德路线规划接口请求失败: {e}"}

        if data.get("status") != "1":
            return {"error": f"规划路线失败: {data.get('info', '未知错误')}"}

        route = data.get("route", {})
        transits = route.get("transits", [])
        if not transits:
            return {"error": f"未能找到从‘{orig}’到‘{dest}’的公共交通换乘方案"}

        best = transits[0]
        cost = best.get("cost", "详见现场")
        duration_sec = int(best.get("duration", 0))
        duration_min = round(duration_sec / 60)
        walk_dist = best.get("walking_distance", "0")

        steps = []
        for seg in best.get("segments", []):
            bus = seg.get("bus", {})
            buslines = bus.get("buslines", [])
            if buslines:
                bl = buslines[0]
                line_name = bl.get("name", "").split("(")[0].strip()
                dep_stop = bl.get("departure_stop", {}).get("name", "")
                arr_stop = bl.get("arrival_stop", {}).get("name", "")
                via_num = bl.get("via_num", "若干")
                steps.append({
                    "line": line_name,
                    "from_station": dep_stop,
                    "to_station": arr_stop,
                    "via_stops": via_num,
                })

        return {
            "city": c,
            "origin": orig,
            "destination": dest,
            "duration": f"约 {duration_min} 分钟",
            "cost": f"¥{cost}" if cost and cost != "[]" else "约 3~6 元",
            "walk_distance": f"{walk_dist} 米",
            "transfer_steps": steps,
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询地铁路线出错：{result['error']}"

        if result.get("status") == "need_key_or_llm_search":
            return f"🚇 正在为您规划从【{result['origin']}】到【{result['destination']}】的地铁公交路线..."

        steps = result.get("transfer_steps", [])
        lines = [
            f"🚇 【{result['city']} 地铁换乘路线】",
            f"• 起点 ➔ 终点：{result['origin']} ➔ {result['destination']}",
            f"• 预计耗时：{result['duration']}（步行约 {result['walk_distance']}，票价 {result['cost']}）",
            f"• 乘坐方案：",
        ]
        for idx, s in enumerate(steps, 1):
            lines.append(f"  {idx}. 乘坐【{s['line']}】：在【{s['from_station']}】上车，途径 {s['via_stops']} 站，在【{s['to_station']}】下车换乘/出站")

        return "\n".join(lines)
