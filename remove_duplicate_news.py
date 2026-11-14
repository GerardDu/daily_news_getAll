#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remove_duplicate_news.py
扫描本地 SQLite 数据库中所有新闻表,按 (title_en, pub_time) 判断重复,
每组重复内容只保留 id 最大(最新入库)的一条,其余删除。

等价于对每张表执行:
    DELETE FROM <表> WHERE id NOT IN (
        SELECT MAX(id) FROM <表> GROUP BY title_en, pub_time
    );
区别在于:title_en 或 pub_time 为 NULL 的记录不参与去重(原语句会把所有 NULL 行
归为一组,只留一条,容易误删)。

特点:
  * 只用 Python 标准库(Python 3.7+)
  * 正式删除前自动备份数据库(使用 SQLite 在线备份接口),可用 --no-backup 关闭
  * 每张表在一个事务内完成,出错自动回滚
  * 表不存在或缺少所需列时自动跳过

用法:
  python remove_duplicate_news.py                  # 使用默认数据库路径
  python remove_duplicate_news.py D:\\news_daily.db   # 指定数据库路径
  python remove_duplicate_news.py --dry-run        # 只统计,不删除、不备份
  python remove_duplicate_news.py --no-backup      # 不备份直接删除
"""
import sqlite3
import sys
from datetime import datetime

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
REQUIRED_COLS = {"id", "title_en", "pub_time"}


def log(msg):
    print(msg, file=sys.stderr)


def table_ready(conn, table):
    """表存在且包含 id、title_en、pub_time 三列才处理。"""
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    if not cols:
        log(f"[跳过] 表 {table} 不存在")
        return False
    missing = REQUIRED_COLS - cols
    if missing:
        log(f"[跳过] 表 {table} 缺少列: {', '.join(sorted(missing))}")
        return False
    return True


# 只对 title_en、pub_time 都不为空的记录去重;每组保留 id 最大的一条
def dup_where(table):
    return (
        f'title_en IS NOT NULL AND pub_time IS NOT NULL AND id NOT IN ('
        f'SELECT MAX(id) FROM "{table}" '
        f'WHERE title_en IS NOT NULL AND pub_time IS NOT NULL '
        f'GROUP BY title_en, pub_time)'
    )


def count_duplicates(conn, table):
    return conn.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE {dup_where(table)}'
    ).fetchone()[0]


def delete_duplicates(conn, table):
    with conn:  # 一张表一个事务,出错自动回滚
        cur = conn.execute(f'DELETE FROM "{table}" WHERE {dup_where(table)}')
    return cur.rowcount


def backup_db(conn, db_path):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"{db_path}.{stamp}.bak"
    dst = sqlite3.connect(bak_path)
    try:
        conn.backup(dst)
    finally:
        dst.close()
    return bak_path


def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in flags
    no_backup = "--no-backup" in flags
    db_path = args[0] if args else DB_PATH

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as ex:
        sys.exit(f"无法打开数据库 {db_path}:{ex}")

    total_found = total_deleted = 0
    try:
        # 先统计所有表的重复数量
        plan = []
        for table in TABLES:
            if not table_ready(conn, table):
                continue
            n = count_duplicates(conn, table)
            plan.append((table, n))
            print(f"[{table}] 发现重复记录 {n} 条")
            total_found += n

        if dry_run:
            print("-" * 50)
            print(f"[dry-run] 共发现 {total_found} 条重复记录,未做任何修改。")
            return
        if total_found == 0:
            print("-" * 50)
            print("没有需要删除的重复记录。")
            return

        # 有重复时才备份
        if not no_backup:
            bak = backup_db(conn, db_path)
            print(f"已备份数据库:{bak}")

        for table, n in plan:
            if n == 0:
                continue
            deleted = delete_duplicates(conn, table)
            total_deleted += deleted
            print(f"[{table}] 已删除 {deleted} 条")
    except sqlite3.Error as ex:
        log(f"[数据库错误] {ex}")
        sys.exit(1)
    finally:
        conn.close()

    print("-" * 50)
    print(f"共发现 {total_found} 条重复记录,实际删除 {total_deleted} 条。")


if __name__ == "__main__":
    main()