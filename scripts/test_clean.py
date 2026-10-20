#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WeChat Vision AI Bot - 纯文本清洗与格式转换验证脚本
用于测试 Markdown 格式去除、LaTeX 表达式转化以及特殊符号处理。
"""

import re


def clean_bot_reply(text):
    if not text:
        return ""

    # 1. 过滤内部联网搜索指令与 JSON 代码块
    text = re.sub(r'\$web_search(?::\d+)?\s*\{[^}]*\}', '', text)
    text = re.sub(r'```(?:json)?\s*\{\s*"query"[\s\S]*?\}\s*```', '', text)

    # 2. LaTeX 块级公式与行内公式定界符剥离
    text = re.sub(r'\$\$([\s\S]*?)\$\$', r'\1', text)
    text = re.sub(r'\\\[([\s\S]*?)\\\]', r'\1', text)
    text = re.sub(r'\$([^\$\n]+?)\$', r'\1', text)
    text = re.sub(r'\\\(([\s\S]*?)\\\)', r'\1', text)

    # 3. 常见 LaTeX 数学结构转换为普通可读纯文本
    text = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'\1/\2', text)
    text = re.sub(r'\\sqrt\{([^{}]+)\}', r'√(\1)', text)
    text = re.sub(r'\\(?:text|mathrm|mathbf|boldsymbol|operatorname|textbf|textit)\{([^{}]+)\}', r'\1', text)
    text = re.sub(r'\^\{([^{}]+)\}', r'^\1', text)
    text = re.sub(r'_\{([^{}]+)\}', r'_\1', text)

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

    # 清理反斜杠转义标点
    text = re.sub(r'\\([%$&_#~])', r'\1', text)

    # 4. Markdown 语法元素清理
    text = re.sub(r'```[a-zA-Z0-9_\-+]*\n?', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*([^\*\n]+?)\*', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'`([^`\n]+?)`', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\)]+)\)', r'\1 (\2)', text)
    text = re.sub(r'^[ \t]*#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*>\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*[-*]\s+', '• ', text, flags=re.MULTILINE)
    text = re.sub(r'^[ \t]*[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)

    # 5. 空行与残留指令清理
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


if __name__ == "__main__":
    test_cases = [
        "**腾讯控股** (00700.HK) 当前股价 $385.4$ 港元，涨幅为 $+1.5\\%$。",
        "牛顿公式：$$F = m \\cdot a$$，其中加速度 $a = \\frac{F}{m} \\approx 9.8 \\text{ m/s}^2$。",
        "### 核心结论\n> 投资需谨慎\n- 关注支撑位：`3000点`\n- 参考来源：[新浪财经](https://finance.sina.com.cn)",
    ]

    print("=== 测试纯文本与格式转换效果 ===")
    for idx, tc in enumerate(test_cases, 1):
        print(f"\n[用例 {idx} 原始输入]:\n{tc}")
        print(f"[用例 {idx} 清洗输出]:\n{clean_bot_reply(tc)}")
