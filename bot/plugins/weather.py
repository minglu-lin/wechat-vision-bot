#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weather Plugin: Open-Meteo Integration
Provides real-time weather and 1-14 day forecast globally with zero API key requirement.
"""

import urllib.request
import urllib.parse
import json
from datetime import datetime
from .base import BasePlugin


WMO_WEATHER_MAP = {
    0: "晴朗",
    1: "晴间多云",
    2: "多云",
    3: "阴天",
    45: "有雾",
    48: "沉积雾",
    51: "轻微毛毛雨",
    53: "中度毛毛雨",
    55: "密集毛毛雨",
    56: "轻微冻毛毛雨",
    57: "浓密冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大暴雨",
    66: "小冻雨",
    67: "大冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪 / 暴雪",
    77: "雪粒",
    80: "微弱阵雨",
    81: "中度阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "大阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴微小冰雹",
    99: "强雷暴伴大冰雹",
}

WEEKDAY_MAP = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class WeatherPlugin(BasePlugin):
    name = "get_weather"
    description = "查询指定城市/地区的当前实时天气及未来 1-14 天逐日天气预报（包括最高/最低气温、天气现象、降水概率、风向风速）"
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市或区县名称，例如：北京、上海、广州、深圳、成都、杭州、朝阳区",
            },
            "days": {
                "type": "integer",
                "description": "预报天数（1至14天），默认为7天",
                "default": 7,
            },
        },
        "required": ["city"],
    }

    fast_patterns = [
        r'^(?:查一下?|看下?)?(?P<city>[\u4e00-\u9fa5]{2,10})的?天气(?:预报)?(?:\s*(?P<days>\d+)天)?$',
        r'^天气\s*(?P<city>[\u4e00-\u9fa5]{2,10})(?:\s*(?P<days>\d+)天)?$',
    ]

    def execute(self, city: str, days: int = 7, **kwargs) -> dict:
        city = (city or "").strip()
        if not city:
            return {"error": "未提供城市名称"}

        try:
            days = int(days)
        except (ValueError, TypeError):
            days = 7
        days = max(1, min(14, days))

        # 1. Geocoding lookup
        encoded_city = urllib.parse.quote(city)
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_city}&count=1&language=zh&format=json"
        try:
            req = urllib.request.Request(geo_url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                geo_data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"城市地址编码解析失败: {e}"}

        results = geo_data.get("results", [])
        if not results:
            return {"error": f"未能找到与‘{city}’相关的地理位置"}

        loc = results[0]
        lat = loc["latitude"]
        lon = loc["longitude"]
        resolved_name = loc.get("name", city)
        admin1 = loc.get("admin1", "")
        country = loc.get("country", "")

        # 2. Weather forecast lookup
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            f"&current_weather=true"
            f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max"
            f"&timezone=Asia%2FShanghai&forecast_days={days}"
        )
        try:
            req2 = urllib.request.Request(weather_url, headers={"User-Agent": "WeChatBot/2.0"})
            with urllib.request.urlopen(req2, timeout=8) as r2:
                w_data = json.loads(r2.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"气象数据接口获取失败: {e}"}

        current = w_data.get("current_weather", {})
        cur_code = current.get("weathercode", 0)
        cur_desc = WMO_WEATHER_MAP.get(cur_code, "晴")
        cur_temp = current.get("temperature", 0.0)
        cur_wind = current.get("windspeed", 0.0)

        daily = w_data.get("daily", {})
        times = daily.get("time", [])
        codes = daily.get("weathercode", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precips = daily.get("precipitation_sum", [])
        precip_probs = daily.get("precipitation_probability_max", [])

        forecast_list = []
        for i in range(len(times)):
            d_code = codes[i] if i < len(codes) else 0
            d_desc = WMO_WEATHER_MAP.get(d_code, "晴")
            d_time = times[i]
            try:
                dt = datetime.strptime(d_time, "%Y-%m-%d")
                weekday = WEEKDAY_MAP[dt.weekday()]
                date_str = f"{dt.month}月{dt.day}日({weekday})"
            except Exception:
                date_str = d_time

            max_t = max_temps[i] if i < len(max_temps) else "-"
            min_t = min_temps[i] if i < len(min_temps) else "-"
            prob = precip_probs[i] if i < len(precip_probs) and precip_probs[i] is not None else 0
            rain_mm = precips[i] if i < len(precips) else 0.0

            forecast_list.append({
                "date": date_str,
                "weather": d_desc,
                "temp_range": f"{min_t}°C ~ {max_t}°C",
                "max_temp": max_t,
                "min_temp": min_t,
                "precipitation_probability": f"{prob}%",
                "rainfall_mm": f"{rain_mm}mm" if rain_mm else "无雨",
            })

        return {
            "city": resolved_name,
            "region": f"{country} {admin1}".strip(),
            "current": {
                "weather": cur_desc,
                "temperature": f"{cur_temp}°C",
                "windspeed": f"{cur_wind} km/h",
            },
            "forecast_days": len(forecast_list),
            "forecast": forecast_list,
        }

    def format_fast_reply(self, result: dict) -> str:
        if "error" in result:
            return f"查询天气出错：{result['error']}"

        city = result.get("city", "")
        region = result.get("region", "")
        cur = result.get("current", {})
        forecast = result.get("forecast", [])

        lines = [
            f"🌤️ 【{city}天气预报】" + (f" ({region})" if region else ""),
            f"• 实时气温：{cur.get('weather', '晴')}，{cur.get('temperature', '-')}，风速 {cur.get('windspeed', '-')}",
            f"• 未来 {len(forecast)} 天预报：",
        ]
        for f in forecast:
            prob_str = f"降水概率{f['precipitation_probability']}" if f.get('precipitation_probability') != "0%" else "无降雨"
            lines.append(f"  - {f['date']}：{f['weather']}，{f['temp_range']}，{prob_str}")

        return "\n".join(lines)
