#!/usr/bin/env python3
"""Generate one dated daily current-affairs report for the GitHub Actions job."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
TIMEZONE = ZoneInfo("Asia/Shanghai")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")


def api_call(prompt: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    payload = {
        "model": MODEL,
        "tools": [{"type": "web_search"}],
        "input": prompt,
        "max_output_tokens": 24000,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"OpenAI API HTTP {error.code}: {detail}") from error

    output_text = data.get("output_text")
    if output_text:
        return output_text
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                chunks.append(content.get("text", ""))
    if not chunks:
        raise RuntimeError("OpenAI API returned no text output")
    return "\n".join(chunks)


def extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("Model output did not contain a JSON object")
    try:
        result = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Model JSON could not be parsed: {error}") from error
    if not isinstance(result, dict) or not isinstance(result.get("markdown"), str):
        raise RuntimeError("Model JSON did not contain a markdown string")
    return result


def inline_markdown(text: str) -> str:
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1 ↗</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def slug(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", plain).strip("-")[:100]


def markdown_to_html(markdown: str) -> str:
    content = re.sub(r"^---[\s\S]*?---\s*", "", markdown, count=1)
    lines = content.splitlines()
    output, paragraph = [], []

    def flush() -> None:
        if paragraph:
            output.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            flush()
            index += 1
            continue
        if line.startswith("# "):
            flush()
            title = line[2:].strip()
            output.append(f'<h1 id="s-{slug(title)}">{inline_markdown(title)}</h1>')
            index += 1
            continue
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            output.append(f'<h2 id="s-{slug(title)}">{inline_markdown(title)}</h2>')
            index += 1
            continue
        if line.startswith("### "):
            flush()
            title = line[4:].strip()
            output.append(f'<h3 id="s-{slug(title)}">{inline_markdown(title)}</h3>')
            index += 1
            continue
        if line.startswith("> "):
            flush()
            quote = []
            while index < len(lines) and lines[index].startswith("> "):
                quote.append(lines[index][2:])
                index += 1
            output.append("<blockquote>" + "".join(f"<p>{inline_markdown(item)}</p>" for item in quote) + "</blockquote>")
            continue
        if line.startswith("|"):
            flush()
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                rows.append([cell.strip() for cell in lines[index].strip().split("|")[1:-1]])
                index += 1
            if len(rows) >= 2:
                header = rows[0]
                data = rows[2:]
                table = '<div class="table-wrap"><table><thead><tr>'
                table += "".join(f"<th>{inline_markdown(cell)}</th>" for cell in header)
                table += "</tr></thead><tbody>"
                for row in data:
                    table += "<tr>" + "".join(f"<td>{inline_markdown(cell)}</td>" for cell in row) + "</tr>"
                output.append(table + "</tbody></table></div>")
            continue
        paragraph.append(line.strip())
        index += 1
    flush()
    return "\n".join(output)


def template_html() -> tuple[str, str]:
    candidates = sorted(ROOT.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"))
    if not candidates:
        raise RuntimeError("No dated HTML template found in the repository")
    template = candidates[-1].read_text(encoding="utf-8")
    style_match = re.search(r"<style>\n([\s\S]*?)\n</style>", template)
    script_match = re.search(r"<script>\n([\s\S]*?)\n</script>", template)
    if not style_match or not script_match:
        raise RuntimeError("Existing HTML template does not contain expected style/script blocks")
    return style_match.group(1), script_match.group(1)


def render_html(markdown: str, report_date: str, style: str, script: str) -> str:
    body = markdown_to_html(markdown)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{report_date}时政日报</title>
<style>
{style}
</style>
</head>
<body>
<div class="page">
<header class="masthead">
  <div><div class="eyebrow">CURRENT AFFAIRS · {report_date.replace('-', '.')}</div><h1>时政日报</h1><p class="subtitle">聚焦国家政策、重大文件、会议通告、时事评论与政治理论原文</p></div>
  <div class="tools"><button id="theme">夜间阅读</button><button onclick="window.print()">打印 / 导出 PDF</button></div>
</header>
<section class="countdown" aria-label="考试倒计时">
  <div class="count-card"><div class="count-label">国考</div><div class="count-number" data-target="2026-11-30">—</div><div class="count-date">目标日期：2026年11月30日</div></div>
  <div class="count-card"><div class="count-label">湖北省考</div><div class="count-number" data-target="2027-03-13">—</div><div class="count-date">目标日期：2027年3月13日</div></div>
  <div class="count-card"><div class="count-label">事业编</div><div class="count-number" data-target="2027-03-27">—</div><div class="count-date">目标日期：2027年3月27日</div></div>
</section>
<div class="toolbar"><div class="search"><input id="search" type="search" placeholder="搜索关键词，如：十五五、生态环境、算法治理"></div><div class="hint">正文来源已保留原文链接</div></div>
<article id="article">
{body}
<div class="print-note">本页面由 {report_date}.md 可视化生成；倒计时按打开页面时的本地日期动态计算。</div>
</article>
</div>
<button class="top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" aria-label="返回顶部">↑</button>
<script>
{script}
</script>
</body>
</html>
'''


def main() -> None:
    requested_date = os.environ.get("REPORT_DATE", "").strip()
    if requested_date:
        try:
            report_day = date.fromisoformat(requested_date)
        except ValueError as error:
            raise RuntimeError("REPORT_DATE must use YYYY-MM-DD format") from error
    else:
        report_day = datetime.now(TIMEZONE).date()
    report_date = report_day.isoformat()
    focus_date = (report_day - timedelta(days=1)).isoformat()
    prompt = f"""你正在为中国公务员考试和事业编备考者生成 {report_date} 的时政日报，重点回看昨天 {focus_date} 的信息。请使用联网检索，逐条核对原文，不要凭记忆编造新闻、数字、标题或网址。

优先来源：半月谈、人民网、人民网观点、求是网、中国政府网、国务院新闻办公室、中国网、新华社、人民数据、习近平系列重要讲话数据库，以及其他中央和国家机关官网。重点筛选国家政策、重大政策文件、中央和国务院会议通告、政策解读、时事评论和对考试有价值的社会治理动态。

请输出一个 JSON 对象，只有两个字段：title 和 markdown。markdown 字段不要包裹代码围栏，不要再写 YAML frontmatter，也不要包含一级标题。markdown 必须以“## 二、昨日及今晨重点时政动态”开始，并按以下要求组织：

1. 时政主体筛选20—30条，每条说明不少于300个中文字符；每条包含信息日期、关键词和原文链接。关键词用 HTML <mark>关键词</mark> 标记。说明要准确区分报道日期、事件日期和政策发布日期。
2. 时政内容下方加入“## 三、政治理论学习：习近平重要讲话原文”。重点覆盖“十五五”规划、习近平新时代中国特色社会主义思想、党的创新理论、中国式现代化、高质量发展、新质生产力、全过程人民民主、总体国家安全观、马克思主义与党史等。政治理论文段必须从习近平系列重要讲话数据库、人民数据、中央委员会全体会议等权威原文中逐字摘录，保持原有标点和措辞，不得改写、概括、润色或拼接；每段标明来源链接。
3. 最后加入“## 四、来源索引”，说明使用的权威来源范围。
4. 不要重复完全相同的新闻；不要把无法核验的社交媒体、营销稿或未经证实的传闻当作来源。若某条信息只能部分核验，宁可删掉，不要补写猜测。
5. 保持政策学习和公考备考导向，避免空泛评论。输出的 Markdown 应可直接转换成页面正文。
"""
    result = extract_json(api_call(prompt))
    markdown_body = result["markdown"].strip()
    markdown_body = re.sub(r"^# .+?\n+", "", markdown_body, count=1)
    markdown = f'''---
title: {report_date}时政日报
date: {report_date}
focus_date: {focus_date}
description: 聚焦国家政策、重大政策文件、会议通告、时事评论与政治理论原文
---

# {report_date}时政日报

> **重点回看日期：{focus_date}。** 本期重点整理昨天发布、发生或公开披露的权威信息，并保留与近期政策学习和公考备考相关的理论材料。

## 一、考试倒计时

| 考试项目 | 目标日期 | 说明 |
| --- | --- | --- |
| 国考 | 2026年11月30日 | 目标日期按既定配置 |
| 湖北省考 | 2027年3月13日 | 目标日期按既定配置 |
| 事业编 | 2027年3月27日 | 目标日期按既定配置 |

> HTML 页面会根据打开时的本地时间自动刷新倒计时。

{markdown_body}
'''
    style, script = template_html()
    (ROOT / f"{report_date}.md").write_text(markdown, encoding="utf-8")
    (ROOT / f"{report_date}.html").write_text(render_html(markdown, report_date, style, script), encoding="utf-8")
    print(f"Generated {report_date}.md and {report_date}.html; focus date {focus_date}.")


if __name__ == "__main__":
    main()
