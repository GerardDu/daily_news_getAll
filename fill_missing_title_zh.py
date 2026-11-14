#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fill_missing_title_zh.py
扫描本地 SQLite 数据库中所有新闻表,找出 title_zh 为空(NULL / 空字符串 / 只有空格)的记录,
读取对应的 title_en 翻译成中文后,写回该记录的 title_zh。

特点:
  * 只用 Python 标准库(Python 3.7+)
  * 翻译服务沿用 bloomberg_flash_on_china_to_sqlite.py:先 Google 接口,失败再 MyMemory 接口
  * 每翻译成功一条就提交一次,中途中断(Ctrl+C / 断网)也不会丢失已完成的结果
  * 翻译失败的记录保持为空,下次运行会再次尝试
  * 自动读取 HTTP_PROXY / HTTPS_PROXY 环境变量

用法:
  python fill_missing_title_zh.py              # 使用默认数据库路径
  python fill_missing_title_zh.py D:\\news_daily.db   # 指定数据库路径
  python fill_missing_title_zh.py --dry-run    # 只统计,不翻译、不写入
"""
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

DB_PATH = r"D:\news_daily.db"   # 数据库文件位置(按需修改)
TABLES = [
    "ap_news",
    "bloomberg_news",
    "economist_news",
    "ft_news",
    "nytimes_news",
    "reuters_news",
    "wsj_news",
]
TIMEOUT = 15
SLEEP_SEC = 0.3     # 每条翻译之间的间隔,避免请求过快被限流
UA = {"User-Agent": "Mozilla/5.0 (compatible; fill-title-zh/1.0)"}


def log(msg):
    print(msg, file=sys.stderr)


def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


# ---------------- 翻译(与原程序一致) ----------------
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
            log(f"    [翻译失败] {name}: {type(ex).__name__}: {ex}")
    return None


# ---------------- 数据库操作 ----------------
def table_ready(conn, table):
    """表存在且包含 id、title_en、title_zh 三列才处理。"""
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if not cols:
        log(f"[跳过] 表 {table} 不存在")
        return False
    missing = {"id", "title_en", "title_zh"} - cols
    if missing:
        log(f"[跳过] 表 {table} 缺少列: {', '.join(sorted(missing))}")
        return False
    return True


def fetch_missing(conn, table):
    return conn.execute(
        f'SELECT id, title_en FROM "{table}" '
        f"WHERE (title_zh IS NULL OR TRIM(title_zh) = '') "
        f"AND title_en IS NOT NULL AND TRIM(title_en) <> '' "
        f"ORDER BY id"
    ).fetchall()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv[1:]
    db_path = args[0] if args else DB_PATH

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as ex:
        sys.exit(f"无法打开数据库 {db_path}:{ex}")

    total_found = total_done = total_failed = 0
    try:
        for table in TABLES:
            if not table_ready(conn, table):
                continue
            rows = fetch_missing(conn, table)
            total_found += len(rows)
            print(f"[{table}] 需要补译 {len(rows)} 条")
            if dry_run or not rows:
                continue

            for i, (row_id, title_en) in enumerate(rows, 1):
                zh = translate(title_en.strip())
                if zh:
                    conn.execute(f'UPDATE "{table}" SET title_zh = ? WHERE id = ?',
                                 (zh, row_id))
                    conn.commit()           # 每条提交,中断不丢进度
                    total_done += 1
                    print(f"  ({i}/{len(rows)}) id={row_id}  {title_en[:50]}  ->  {zh[:30]}")
                else:
                    total_failed += 1
                    log(f"  ({i}/{len(rows)}) id={row_id} 翻译失败,保持为空")
                time.sleep(SLEEP_SEC)
    except KeyboardInterrupt:
        print("\n已手动中断,已完成的翻译均已保存。")
    finally:
        conn.close()

    print("-" * 50)
    if dry_run:
        print(f"[dry-run] 共发现 {total_found} 条 title_zh 为空的记录,未做任何修改。")
    else:
        print(f"共发现 {total_found} 条,成功补译 {total_done} 条,失败 {total_failed} 条。")
        if total_failed:
            print("失败的记录保持为空,稍后重新运行本程序即可再次尝试。")


if __name__ == "__main__":
    main()