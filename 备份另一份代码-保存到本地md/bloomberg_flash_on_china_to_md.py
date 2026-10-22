#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bloomberg_flash_v2.py
抓取彭博 RSS 中"标题含中国相关词"的最新 5 条 -> 翻译标题
-> 在当前目录生成 bloomberg_china_flash_<运行时间>.md

筛选方式:彭博 RSS 没有按关键词搜索的功能,所以先抓取多个栏目,
          再检查"标题"是否包含 CHINA_KEYWORDS 里的关键词(只看标题)。

特点:
  * 只用 Python 标准库,不需要 pip 安装任何东西(Python 3.7+)
  * 标题排版:上方英文,下方中文
  * 翻译依次尝试 Google 接口 -> MyMemory 接口,失败会在终端打印原因
  * 自动读取 HTTP_PROXY / HTTPS_PROXY 环境变量(需要代理时使用)
  * 条目时间 = 文章发布时间(转北京时间);文件名时间 = 本次运行时间

用法:
  python bloomberg_flash_v2.py
"""
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://feeds.bloomberg.com/business/news.rss",
    "https://feeds.bloomberg.com/industries/news.rss",
]
LIMIT = 5
PREFIX = "bloomberg_china_flash"

# 中国相关关键词(不区分大小写,按完整单词匹配;可自行增删)
CHINA_KEYWORDS = [
    "china", "chinese", "beijing", "xi jinping", "xi", "shanghai", "shenzhen",
    "yuan", "renminbi", "pboc", "中国",
]
CHINA_RE = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(k) for k in CHINA_KEYWORDS) + r")(?![A-Za-z])",
    re.IGNORECASE,
)
TIMEOUT = 15
TZ = timezone(timedelta(hours=8))  # 北京时间
UA = {"User-Agent": "Mozilla/5.0 (compatible; bloomberg-flash/2.0)"}


def log(msg):
    print(msg, file=sys.stderr)


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# ---------------- 抓取 RSS ----------------
def parse_time(text):
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except Exception:
        pass
    try:  # ISO 8601 兜底
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except Exception:
        return None


def fetch_items():
    seen, items = set(), []
    for url in FEEDS:
        try:
            root = ET.fromstring(http_get(url))
        except Exception as ex:
            log(f"[抓取失败] {url}: {type(ex).__name__}: {ex}")
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            key = link or (it.findtext("guid") or title)
            if not title or key in seen:
                continue
            if not CHINA_RE.search(title):   # 标题中没有中国相关词就跳过
                continue
            seen.add(key)
            pub = parse_time(it.findtext("pubDate"))
            if pub is None:
                for child in it:  # 兼容 dc:date 之类的标签
                    if child.tag.lower().endswith("date"):
                        pub = parse_time(child.text)
                        break
            items.append({"title": title, "link": link, "time": pub})
    floor = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda x: x["time"] or floor, reverse=True)
    if 0 < len(items) < LIMIT:
        log(f"[提示] 当前各栏目中标题含中国相关词的新闻只有 {len(items)} 条,不足 {LIMIT} 条。")
    return items[:LIMIT]


# ---------------- 翻译 ----------------
def tr_google(text):
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
           "&sl=en&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text))
    data = json.loads(http_get(url).decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()


def tr_mymemory(text):
    url = ("https://api.mymemory.translated.net/get?langpair=en|zh-CN&q="
           + urllib.parse.quote(text))
    data = json.loads(http_get(url).decode("utf-8"))
    if str(data.get("responseStatus")) != "200":
        raise RuntimeError(f"MyMemory 返回状态 {data.get('responseStatus')}: "
                           f"{data.get('responseDetails')}")
    return data["responseData"]["translatedText"].strip()


TRANSLATORS = [("Google", tr_google), ("MyMemory", tr_mymemory)]


def translate(text):
    for name, fn in TRANSLATORS:
        try:
            zh = fn(text)
            if zh and zh != text:
                return zh
        except Exception as ex:
            log(f"[翻译失败] {name}: {type(ex).__name__}: {ex}")
    return "(翻译失败,请查看终端报错)"


# ---------------- 生成 Markdown ----------------
def build_md(items, now):
    lines = ["# 快讯", "", f"*生成时间:{now:%Y-%m-%d %H:%M:%S}(北京时间)*", ""]
    for it in items:
        t = it["time"] or now
        lines += [
            "---",
            "",
            f"> **⏰ {t:%Y-%m-%d %H:%M}**",
            ">",
            f"> **{it['title']}**",          # 上:英文
            ">",
            f"> {translate(it['title'])}",   # 下:中文
            ">",
        ]
        lines += [
            f"> [{it['link']}]({it['link']})",
            "",
        ]
    return "\n".join(lines)


def main():
    items = fetch_items()
    if not items:
        sys.exit("未找到与中国相关的新闻:请检查网络(或设置代理),或稍后重试。")
    now = datetime.now(TZ)
    fname = f"{PREFIX}_{now:%Y%m%d_%H%M%S}.md"
    Path(fname).write_text(build_md(items, now), encoding="utf-8")
    print(f"已生成:{Path(fname).resolve()}")


if __name__ == "__main__":
    main()