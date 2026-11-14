#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_news.py
在本地 SQLite 数据库的所有新闻表中,按关键词搜索中文标题(title_zh)和英文标题(title_en),
合并结果后按发布时间倒序显示最新的 N 条。

等价于之前手写的 SQL:
    SELECT source, pub_time, title_en, title_zh, link
    FROM ( ... UNION ALL ... ) AS all_news
    WHERE title_zh LIKE '%关键词%' OR title_en LIKE '%关键词%' ...
    ORDER BY pub_time DESC LIMIT 15;

两种使用方式:
  1) 命令行直接带关键词 -> 查一次就退出(适合快速查询、配合脚本)，默认15条，可用 -n/--limit 改变
  2) 不带关键词直接运行 -> 进入交互模式,可连续查询,直接回车退出，默认15条，可用 -n/--limit 改变
  注：
    交互模式：多个关键词用逗号分隔，例如 黄仁勋, 英伟达, Jensen Huang，这样含空格的关键词也不用加引号。查完可以继续输入下一组，直接回车退出。
    命令行模式：查一次就退出，适合快速查询。

特点:
  * 只用 Python 标准库(Python 3.7+)
  * 数据库以只读方式打开,不会修改数据
  * 关键词使用参数化查询,并自动转义 % 和 _,不会出现 SQL 注入或通配符误匹配
  * 每个关键词同时匹配中文标题和英文标题;多个关键词默认"任意一个命中"(OR),可用 --and 改成"全部命中"
  * 表不存在或缺少所需列时自动跳过
  注：
    英文大小写：SQLite 的 LIKE 对英文字母不区分大小写，所以搜 nvidia 也能匹配 Nvidia、NVIDIA。
    重复内容：查询不会自动去重，如果结果里有重复，先运行 remove_duplicate_news.py 清理即可。

用法:
  python search_news.py                              # 交互模式
  python search_news.py 黄仁勋 英伟达 "Jensen Huang" "Nvidia"  # 直接查询(含空格的词要加引号)
  python search_news.py 英伟达 --and 出口             # 同时包含"英伟达"和"出口"
  python search_news.py 黄仁勋 -n 30                 # 显示 30 条
  python search_news.py 黄仁勋 --csv result.csv      # 同时导出为 CSV，当前文件夹下，编码是 utf-8-sig，用 Excel 打开中文不会乱码。
  python search_news.py --db D:\\news_daily.db 黄仁勋 # 指定数据库路径
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = r"D:\news_daily.db"   # 数据库文件位置(按需修改)
DEFAULT_LIMIT = 15               # 默认显示条数
TABLES = [
    "ap_news",
    "bloomberg_news",
    "economist_news",
    "ft_news",
    "nytimes_news",
    "reuters_news",
    "wsj_news",
]
REQUIRED_COLS = {"pub_time", "title_en", "title_zh", "link"}


def log(msg):
    print(msg, file=sys.stderr)


# ---------------- 数据库 ----------------
def open_readonly(db_path):
    p = Path(db_path)
    if not p.is_file():
        sys.exit(f"找不到数据库文件:{db_path}")
    try:
        return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as ex:
        sys.exit(f"无法打开数据库 {db_path}:{ex}")


def usable_tables(conn):
    """返回存在且包含所需列的表名列表。"""
    ok = []
    for table in TABLES:
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if not cols:
            log(f"[跳过] 表 {table} 不存在")
        elif REQUIRED_COLS - cols:
            log(f"[跳过] 表 {table} 缺少列: {', '.join(sorted(REQUIRED_COLS - cols))}")
        else:
            ok.append(table)
    return ok


def like_pattern(keyword):
    """转义 LIKE 的特殊字符,并在两侧加 %。配合 ESCAPE '\\' 使用。"""
    esc = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def search(conn, tables, keywords, limit, match_all):
    union = "\n    UNION ALL\n    ".join(
        f"SELECT '{t}' AS source, pub_time, title_en, title_zh, link FROM \"{t}\""
        for t in tables
    )
    one = "(title_zh LIKE ? ESCAPE '\\' OR title_en LIKE ? ESCAPE '\\')"
    joiner = " AND " if match_all else " OR "
    where = joiner.join(one for _ in keywords)
    params = []
    for kw in keywords:
        p = like_pattern(kw)
        params += [p, p]
    params.append(limit)
    sql = (
        f"SELECT source, pub_time, title_en, title_zh, link FROM (\n    {union}\n) AS all_news\n"
        f"WHERE {where}\nORDER BY pub_time DESC\nLIMIT ?"
    )
    return conn.execute(sql, params).fetchall()


# ---------------- 输出 ----------------
def show(rows, keywords, match_all):
    mode = "全部包含" if match_all else "任意包含"
    print(f"\n关键词({mode}):{' | '.join(keywords)}")
    if not rows:
        print("没有找到匹配的内容。")
        return
    print(f"找到 {len(rows)} 条(按发布时间倒序):\n")
    for i, (source, pub_time, title_en, title_zh, link) in enumerate(rows, 1):
        print(f"[{i}] {pub_time or '-'}  {source}")
        print(f"    EN: {title_en or ''}")
        print(f"    ZH: {title_zh or '(未翻译)'}")
        print(f"    {link or ''}\n")


def export_csv(rows, path):
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig:Excel 打开不乱码
            w = csv.writer(f)
            w.writerow(["source", "pub_time", "title_en", "title_zh", "link"])
            w.writerows(rows)
        print(f"已导出 {len(rows)} 条到 {path}")
    except OSError as ex:
        log(f"[导出失败] {path}: {ex}")


# ---------------- 交互模式 ----------------
def split_keywords(text):
    """交互输入用逗号/分号/顿号分隔关键词(这样关键词本身可以含空格,如 Jensen Huang)。"""
    return [k.strip() for k in re.split(r"[,,;;、]", text) if k.strip()]


def interactive(conn, tables, limit, match_all):
    print("交互模式:输入关键词查询,多个关键词用逗号分隔(如:黄仁勋, 英伟达, Jensen Huang)。")
    print("直接回车退出。\n")
    while True:
        try:
            text = input("关键词> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            break
        keywords = split_keywords(text)
        if keywords:
            show(search(conn, tables, keywords, limit, match_all), keywords, match_all)


def main():
    ap = argparse.ArgumentParser(description="在所有新闻表中按关键词搜索标题")
    ap.add_argument("keywords", nargs="*", help="关键词(含空格的词请加引号);不填则进入交互模式")
    ap.add_argument("--db", default=DB_PATH, help=f"数据库路径(默认 {DB_PATH})")
    ap.add_argument("-n", "--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"最多显示条数(默认 {DEFAULT_LIMIT})")
    ap.add_argument("--and", dest="match_all", action="store_true",
                    help="多个关键词需全部命中(默认任意一个命中)")
    ap.add_argument("--csv", metavar="FILE", help="同时把结果导出为 CSV 文件")
    args = ap.parse_intermixed_args()

    if args.limit <= 0:
        sys.exit("-n/--limit 必须是正整数")

    try:                                    # 避免 Windows 控制台遇到特殊字符时报错
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    conn = open_readonly(args.db)
    try:
        tables = usable_tables(conn)
        if not tables:
            sys.exit("没有可查询的表,请检查数据库。")
        if args.keywords:
            rows = search(conn, tables, args.keywords, args.limit, args.match_all)
            show(rows, args.keywords, args.match_all)
            if args.csv:
                export_csv(rows, args.csv)
        else:
            if args.csv:
                log("[提示] 交互模式下忽略 --csv,请在命令行直接带关键词使用。")
            interactive(conn, tables, args.limit, args.match_all)
    except sqlite3.Error as ex:
        sys.exit(f"[数据库错误] {ex}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()