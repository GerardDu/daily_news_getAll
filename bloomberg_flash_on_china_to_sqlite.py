#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bloomberg_flash_on_china_to_sqlite.py
抓取彭博 RSS 中"标题含中国相关词"的最新 5 条 -> 翻译标题
-> 写入 SQLite 数据库 D:\news_daily.db 的 bloomberg_news 表
   (库或表不存在则自动创建;已存在则直接追加,按链接去重)

筛选方式:彭博 RSS 没有按关键词搜索的功能,所以先抓取多个栏目,
          再检查"标题"是否包含 CHINA_KEYWORDS 里的关键词(只看标题)。

特点:
  * 只用 Python 标准库,不需要 pip 安装任何东西(Python 3.7+)
  * 标题排版:上方英文,下方中文
  * 翻译依次尝试 Google 接口 -> MyMemory 接口,失败会在终端打印原因
  * 自动读取 HTTP_PROXY / HTTPS_PROXY 环境变量(需要代理时使用)
  * pub_time = 文章发布时间(北京时间);fetched_at = 本次运行时间(北京时间)

用法:
  python bloomberg_flash_on_china_to_sqlite.py
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

FEEDS = [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://feeds.bloomberg.com/technology/news.rss",
    "https://feeds.bloomberg.com/business/news.rss",
    "https://feeds.bloomberg.com/industries/news.rss",
]
LIMIT = 5
DB_PATH = r"D:\news_daily.db"   # 数据库文件位置
TABLE = "bloomberg_news"          # 表名

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
    return None


# ---------------- 写入 SQLite ----------------
def init_db(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pub_time   TEXT,               -- 发布时间(北京时间)
            title_en   TEXT NOT NULL,      -- 英文标题
            title_zh   TEXT,               -- 中文标题(翻译失败时为 NULL,下次运行会补译)
            link       TEXT NOT NULL UNIQUE,
            fetched_at TEXT NOT NULL       -- 抓取时间(北京时间)
        )""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_pub ON {TABLE}(pub_time)")


def save_items(conn, items, now):
    """新链接 -> 插入;已存在但中文标题为空 -> 补译更新;其余跳过。"""
    inserted = updated = skipped = 0
    fetched_at = f"{now:%Y-%m-%d %H:%M:%S}"
    for it in items:
        row = conn.execute(f"SELECT id, title_zh FROM {TABLE} WHERE link = ?",
                           (it["link"],)).fetchone()
        if row and row[1]:                      # 已有且已翻译 -> 跳过
            skipped += 1
            continue
        zh = translate(it["title"])             # 只对需要的条目调用翻译
        if row:                                 # 已有但缺中文 -> 补上
            conn.execute(f"UPDATE {TABLE} SET title_zh = ? WHERE id = ?", (zh, row[0]))
            updated += 1
        else:
            t = it["time"] or now
            conn.execute(
                f"INSERT INTO {TABLE} (pub_time, title_en, title_zh, link, fetched_at) "
                f"VALUES (?, ?, ?, ?, ?)",
                (f"{t:%Y-%m-%d %H:%M:%S}", it["title"], zh, it["link"], fetched_at))
            inserted += 1
    return inserted, updated, skipped


def main():
    items = fetch_items()
    if not items:
        sys.exit("未找到与中国相关的新闻:请检查网络(或设置代理),或稍后重试。")
    now = datetime.now(TZ)
    try:
        conn = sqlite3.connect(DB_PATH)         # 文件不存在会自动创建
    except sqlite3.Error as ex:
        sys.exit(f"无法打开数据库 {DB_PATH}:{ex}\n请确认 D 盘存在且有写入权限。")
    try:
        with conn:                              # 自动提交/回滚
            init_db(conn)
            ins, upd, skip = save_items(conn, items, now)
    finally:
        conn.close()
    print(f"数据库:{DB_PATH}  表:{TABLE}")
    print(f"新增 {ins} 条,补译 {upd} 条,已存在跳过 {skip} 条。")


if __name__ == "__main__":
    main()