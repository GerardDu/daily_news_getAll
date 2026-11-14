#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nytimes_on_china_to_sqlite.py
抓取《纽约时报》RSS 中标题含中国相关词的最新 5 条
-> 翻译标题 -> 写入 SQLite 数据库 D:\\news_daily.db 的 nytimes_news 表

说明:
  * 使用《纽约时报》公开的 RSS 栏目源（世界、亚太、商业、科技等）。
  * NYT RSS 没有通用关键词搜索接口，因此先抓取多个栏目，再按标题关键词筛选。
  * 只使用 Python 标准库，不需要 pip 安装第三方包。
  * 翻译依次尝试 Google 翻译接口 -> MyMemory 接口。
  * 自动读取 HTTP_PROXY / HTTPS_PROXY 环境变量（需要代理时使用）。
  * pub_time 和 fetched_at 均使用北京时间。
  * 按文章链接去重；翻译失败的记录下次运行时会尝试补译。

用法:
  python nytimes_on_china_to_sqlite.py
"""

import json
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# 《纽约时报》RSS 栏目源。各栏目可能有重复文章，程序会按链接去重。
FEEDS = [
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/AsiaPacific.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    # 《纽约时报》China 主题 RSS；若该地址暂时不可用，其他栏目仍会继续抓取
    "https://www.nytimes.com/svc/collections/v1/publish/http://www.nytimes.com/topic/destination/china/rss.xml",
]

LIMIT = 5
DB_PATH = r"D:\\news_daily.db"
TABLE = "nytimes_news"

# 中国相关关键词（不区分大小写；英文按完整单词匹配）
CHINA_KEYWORDS = [
    "china", "chinese", "beijing", "xi jinping", "shanghai", "shenzhen",
    "yuan", "renminbi", "pboc", "prc", "mainland china", "taiwan",
    "hong kong", "中国", "北京", "上海",
]
CHINA_RE = re.compile(
    r"(?<![A-Za-z])(" +
    "|".join(re.escape(k) for k in CHINA_KEYWORDS) +
    r")(?![A-Za-z])",
    re.IGNORECASE,
)

TIMEOUT = 20
TZ = timezone(timedelta(hours=8))  # 北京时间
UA = {"User-Agent": "Mozilla/5.0 (compatible; nytimes-china/1.0)"}


def log(msg):
    print(msg, file=sys.stderr)


def http_get(url):
    """urllib 会自动读取 HTTP_PROXY / HTTPS_PROXY 等代理环境变量。"""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# ---------------- 解析 RSS ----------------
def parse_time(text):
    if not text:
        return None

    try:
        dt = parsedate_to_datetime(text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
        pass

    try:  # ISO 8601 兜底
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except (TypeError, ValueError, OverflowError):
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
            guid = (it.findtext("guid") or "").strip()
            key = link or guid or title

            if not title or key in seen:
                continue

            # 只按标题筛选，避免摘要中偶然出现 China 就被误判
            if not CHINA_RE.search(title):
                continue

            seen.add(key)
            pub = parse_time(it.findtext("pubDate"))

            # 部分 RSS 使用 dc:date 等日期标签
            if pub is None:
                for child in it:
                    if child.tag.lower().endswith("date"):
                        pub = parse_time(child.text)
                        if pub is not None:
                            break

            items.append({"title": title, "link": link, "time": pub})

    floor = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda x: x["time"] or floor, reverse=True)

    if 0 < len(items) < LIMIT:
        log(f"[提示] 当前找到标题含中国相关词的纽约时报新闻只有 {len(items)} 条，不足 {LIMIT} 条。")

    return items[:LIMIT]


# ---------------- 翻译 ----------------
def tr_google(text):
    url = (
        "https://translate.googleapis.com/translate_a/single?client=gtx"
        "&sl=en&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text)
    )
    data = json.loads(http_get(url).decode("utf-8"))
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()


def tr_mymemory(text):
    url = (
        "https://api.mymemory.translated.net/get?langpair=en|zh-CN&q="
        + urllib.parse.quote(text)
    )
    data = json.loads(http_get(url).decode("utf-8"))
    if str(data.get("responseStatus")) != "200":
        raise RuntimeError(
            f"MyMemory 返回状态 {data.get('responseStatus')}: "
            f"{data.get('responseDetails')}"
        )
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
    return None


# ---------------- 写入 SQLite ----------------
def init_db(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pub_time   TEXT,               -- 发布时间（北京时间）
            title_en   TEXT NOT NULL,      -- 英文标题
            title_zh   TEXT,               -- 中文标题（翻译失败时为 NULL）
            link       TEXT NOT NULL UNIQUE,
            fetched_at TEXT NOT NULL       -- 抓取时间（北京时间）
        )
    """)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_pub ON {TABLE}(pub_time)"
    )


def save_items(conn, items, now):
    """新链接 -> 插入；已存在但中文标题为空 -> 补译更新；其余跳过。"""
    inserted = updated = skipped = 0
    fetched_at = f"{now:%Y-%m-%d %H:%M:%S}"

    for it in items:
        row = conn.execute(
            f"SELECT id, title_zh FROM {TABLE} WHERE link = ?",
            (it["link"],),
        ).fetchone()

        if row and row[1]:
            skipped += 1
            continue

        zh = translate(it["title"])

        if row:
            conn.execute(
                f"UPDATE {TABLE} SET title_zh = ? WHERE id = ?",
                (zh, row[0]),
            )
            updated += 1
        else:
            pub_time = it["time"] or now
            conn.execute(
                f"INSERT INTO {TABLE} "
                "(pub_time, title_en, title_zh, link, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    f"{pub_time:%Y-%m-%d %H:%M:%S}",
                    it["title"],
                    zh,
                    it["link"],
                    fetched_at,
                ),
            )
            inserted += 1

    return inserted, updated, skipped


def main():
    items = fetch_items()
    if not items:
        sys.exit(
            "未找到标题含中国相关词的纽约时报新闻。请检查网络/代理、"
            "RSS 地址是否可用，或稍后重试。"
        )

    now = datetime.now(TZ)

    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as ex:
        sys.exit(
            f"无法打开数据库 {DB_PATH}: {ex}\\n"
            "请确认 D 盘存在且有写入权限。"
        )

    try:
        with conn:
            init_db(conn)
            inserted, updated, skipped = save_items(conn, items, now)
    finally:
        conn.close()

    print(f"数据库: {DB_PATH}  表: {TABLE}")
    print(f"本次筛选到 {len(items)} 条新闻。")
    print(f"新增 {inserted} 条，补译 {updated} 条，已存在跳过 {skipped} 条。")


if __name__ == "__main__":
    main()
