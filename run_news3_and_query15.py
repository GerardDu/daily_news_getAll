#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_news3_and_query15.py
依次执行三个抓取程序(路透社 -> 华尔街日报 -> 彭博),
全部执行完后,从 D:\\news_daily.db 中合并三张表,显示最新 15 条。

要求:本文件与三个抓取程序放在同一个目录下。
用法:python run_news3_and_query15.py
"""
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS = [
    "reuters_flash_on_china_to_sqlite.py",
    "wsj_flash_on_china_to_sqlite.py",
    "bloomberg_flash_on_china_to_sqlite.py",
]
DB_PATH = r"D:\news_daily.db"
# (来源标签, 表名),顺序即合并顺序
TABLES = [
    ("bloomberg", "bloomberg_news"),
    ("wsj", "wsj_news"),
    ("reuters", "reuters_news"),
]
LIMIT = 15
SCRIPT_TIMEOUT = 300  # 单个程序最长运行秒数


def run_scripts():
    """逐个执行抓取程序;某个失败不影响后面的。返回 [(脚本名, 状态)]。"""
    results = []
    for name in SCRIPTS:
        path = SCRIPT_DIR / name
        print(f"\n{'=' * 60}\n>>> 正在执行:{name}\n{'=' * 60}", flush=True)
        if not path.exists():
            print(f"[跳过] 找不到文件:{path}", flush=True)
            results.append((name, "文件不存在"))
            continue
        start = time.time()
        try:
            proc = subprocess.run([sys.executable, str(path)],
                                  cwd=str(SCRIPT_DIR), timeout=SCRIPT_TIMEOUT)
            status = "成功" if proc.returncode == 0 else f"失败(退出码 {proc.returncode})"
        except subprocess.TimeoutExpired:
            status = f"超时(>{SCRIPT_TIMEOUT}秒)"
        except Exception as ex:
            status = f"出错:{type(ex).__name__}: {ex}"
        print(f"--- {name}:{status},用时 {time.time() - start:.1f} 秒", flush=True)
        results.append((name, status))
    return results


def show_latest():
    """合并三张表,按发布时间倒序显示最新 LIMIT 条。"""
    print(f"\n{'=' * 60}\n>>> 最新 {LIMIT} 条新闻(三家合并)\n{'=' * 60}")
    if not Path(DB_PATH).exists():
        print(f"数据库不存在:{DB_PATH}(三个抓取程序都没有成功写入数据)")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        parts = [f"SELECT '{src}' AS src, pub_time, title_en, title_zh FROM {tbl}"
                 for src, tbl in TABLES if tbl in existing]
        missing = [tbl for _, tbl in TABLES if tbl not in existing]
        if missing:
            print(f"[提示] 以下表还不存在,已忽略:{', '.join(missing)}")
        if not parts:
            print("没有可查询的表。")
            return
        sql = " UNION ALL ".join(parts) + " ORDER BY pub_time DESC LIMIT ?"
        rows = conn.execute(sql, (LIMIT,)).fetchall()
    finally:
        conn.close()

    if not rows:
        print("表里还没有数据。")
        return
    for i, (src, pub, en, zh) in enumerate(rows, 1):
        print(f"\n{i:>2}. [{src}] {pub}")
        print(f"    {en}")
        print(f"    {zh if zh else '(中文标题暂缺)'}")


def main():
    # 避免个别终端编码不支持中文时直接报错退出
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    results = run_scripts()
    show_latest()
    print(f"\n{'=' * 60}\n>>> 执行情况汇总\n{'=' * 60}")
    for name, status in results:
        print(f"  {name}: {status}")


if __name__ == "__main__":
    main()