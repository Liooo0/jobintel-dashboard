#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成合成脱敏的 demo 数据库：demo/jobintel_demo.db。

为什么需要它
------------
本项目真实数据（4.7 万条投递记录）属于个人求职隐私，不入公开仓库。为了让
clone 仓库的人无需真实数据也能把仪表盘跑起来，这里生成一份 **完全合成** 的
demo 数据：表结构、字段含义、取值分布与真实库一致，但公司名 / 岗位名 / 城市
以外的文本全部来自本文件内置的合成词表，与任何真实记录无关。

字段分布参考真实库的聚合统计（仅 count / distinct / 分位数 / 各分类占比），
在下面以权重常量的形式表达；本脚本不读取、也不依赖真实库，任何机器 clone 后
都能独立运行。

用法
----
    python3 scripts/make_demo_db.py                      # 默认：1200 行，seed 固定
    python3 scripts/make_demo_db.py --rows 200 --seed 1
    python3 scripts/make_demo_db.py --end-date 2026-09-30 --out /tmp/demo.db

固定 seed + 固定 end-date 时输出可复现（逐行数据完全相同），方便测试与复核。
未指定 --end-date 时以运行当天为最后一天，保证「近 7 天」图表有数据。
"""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "demo" / "jobintel_demo.db"
DEFAULT_ROWS = 1200
DEFAULT_SEED = 20260801
DEFAULT_SPAN_DAYS = 90

# ---------------------------------------------------------------------------
# 合成词表（全部自造，不来自真实库）
# ---------------------------------------------------------------------------
COMPANY_PREFIXES = [
    "星辰", "云澜", "灵犀", "瀚海", "星野", "微光", "启元", "知行", "恒穹", "青柚",
    "拾光", "沧澜", "未名", "竹石", "白鹭", "归谷", "长风", "木青", "数策", "朗星",
    "千帆", "半山", "沐禾", "云栖", "时序", "星轨", "鹿鸣", "青梧", "远瞻", "听澜",
    "北屿", "南乔", "知远", "澄明", "九章", "重构", "流火", "观星", "简一", "云阶",
]
COMPANY_SUFFIXES = [
    "科技", "数据", "智能", "网络", "软件", "信息", "数码", "云图", "数科", "智慧",
    "互联", "物联",
]
COMPANY_POOL = [p + s for p in COMPANY_PREFIXES for s in COMPANY_SUFFIXES]

JOB_LEVELS = ["", "初级", "中级", "高级", "资深"]
JOB_ROLES = [
    "Python后端工程师", "Java后端工程师", "Go后端工程师", "C++开发工程师",
    "前端工程师", "全栈工程师", "Node.js工程师", "数据分析师", "数据工程师",
    "算法工程师", "机器学习工程师", "推荐算法工程师", "大模型算法工程师",
    "NLP算法工程师", "计算机视觉工程师", "运维工程师", "SRE工程师", "测试工程师",
    "自动化测试工程师", "产品经理", "项目经理", "UI设计师", "交互设计师",
    "数据产品经理", "安全工程师", "嵌入式软件工程师", "Android开发工程师",
    "iOS开发工程师", "小程序开发工程师", "爬虫工程师", "量化研究员",
    "风控建模工程师", "BI工程师", "数据仓库工程师", "ETL工程师", "云原生工程师",
    "DevOps工程师", "性能优化工程师", "架构师", "技术专家", "解决方案工程师",
    "售前工程师", "技术支持工程师", "实施工程师", "运营数据分析师",
    "增长工程师", "Rust开发工程师", "PHP开发工程师", "C#开发工程师",
    "Unity开发工程师",
]
JOB_POOL = [lv + r for lv in JOB_LEVELS for r in JOB_ROLES]

CITY_POOL = [
    "深圳", "北京", "上海", "杭州", "广州", "成都", "武汉", "南京",
    "西安", "苏州", "长沙", "合肥", "重庆", "郑州", "天津", "厦门",
]
CITY_WEIGHTS = [
    22, 14, 13, 12, 9, 7, 5, 4, 3, 3, 2, 2, 1, 1, 1, 1,
]

KEYWORD_POOL = [
    "Python", "Java", "Go", "前端", "后端", "数据分析", "算法", "大模型",
    "NLP", "运维", "测试", "产品", "安全", "云原生", "大数据", "嵌入式",
    "设计", "C++", "Rust", "量化",
]

# 状态 / 来源 / 薪资单位权重：取真实库聚合占比的近似值
STATUS_WEIGHTS = [("skipped", 0.82), ("applied", 0.165), ("failed", 0.015)]
SOURCE_WEIGHTS = [("boss", 0.945), ("51job", 0.05), ("liepin", 0.005)]
# 薪资单位：真实库以 K 为主，其次元/天、缺失、万；元/月 极少
UNIT_WEIGHTS_APPLIED = [("K", 0.81), ("元/天", 0.10), ("万", 0.06), ("元/月", 0.01), (None, 0.02)]
UNIT_WEIGHTS_SKIPPED = [("K", 0.68), ("元/天", 0.15), (None, 0.11), ("万", 0.06), ("元/月", 0.0)]

SALARY_MONTHS_WEIGHTS = [(None, 0.81), (13, 0.08), (14, 0.045), (15, 0.04), (16, 0.025)]

SKIP_REASONS = [
    "薪资低于预期", "城市不匹配", "岗位方向不符", "公司规模不匹配",
    "要求年限超出", "外包/派遣岗位", "重复岗位", "非目标行业",
    "岗位已关闭", "学历要求不符",
]
APPLY_REASONS = [
    "符合筛选条件，已自动投递",
    "关键词命中，评分达标",
    "薪资与城市均匹配",
]
FAIL_REASONS = [
    "投递过程出现异常，已记录",
]
FAIL_ERRORS = [
    "页面元素定位超时",
    "按钮点击无响应",
    "登录态失效",
    "网络请求失败",
]

COLUMNS = [
    "id", "source", "status", "city", "company", "job",
    "salary_raw", "salary_min_k", "salary_max_k", "salary_unit", "salary_months",
    "score", "keyword", "reason", "error", "ts",
]

CREATE_APPLICATIONS_SQL = """
CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,          -- boss / 51job / liepin
    status TEXT NOT NULL,          -- applied / skipped / failed
    city TEXT, company TEXT, job TEXT,
    salary_raw TEXT,
    salary_min_k REAL, salary_max_k REAL, salary_unit TEXT, salary_months INTEGER,
    score REAL, keyword TEXT, reason TEXT, error TEXT,
    ts TEXT
)
"""

CREATE_META_SQL = """
CREATE TABLE demo_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _weighted(rng: random.Random, pairs):
    """按权重抽一个值；权重和无需为 1。"""
    total = sum(w for _, w in pairs)
    x = rng.random() * total
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if x < acc:
            return value
    return pairs[-1][0]


def _allocate_days(rng: random.Random, rows: int, span_days: int, end: date) -> list[int]:
    """把 rows 行分配到 span_days 天：工作日多、周末少，每天至少 1 行。"""
    weights = []
    for i in range(span_days):
        day = end - timedelta(days=span_days - 1 - i)
        weekday_factor = 1.0 if day.weekday() < 5 else 0.45
        weights.append(weekday_factor * rng.uniform(0.75, 1.25))
    total_w = sum(weights)
    counts = [max(1, int(round(rows * w / total_w))) for w in weights]

    diff = rows - sum(counts)
    i = 0
    while diff != 0:
        idx = i % span_days
        if diff > 0:
            counts[idx] += 1
            diff -= 1
        elif counts[idx] > 1:
            counts[idx] -= 1
            diff += 1
        i += 1
    return counts


def _make_salary(rng: random.Random, status: str):
    """返回 (salary_raw, min_k, max_k, unit, months)，分布参考真实库近似占比。"""
    unit = _weighted(rng, UNIT_WEIGHTS_APPLIED if status == "applied" else UNIT_WEIGHTS_SKIPPED)
    months = _weighted(rng, SALARY_MONTHS_WEIGHTS)

    if unit is None:
        return rng.choice(["", "面议"]), None, None, None, months

    if unit == "K":
        lo = round(rng.triangular(3, 40, 9), 1)
        hi = round(lo + rng.uniform(1.5, max(3.0, lo * 0.6)), 1)
        raw = "%g-%gK" % (lo, hi)
    elif unit == "万":
        lo = round(rng.uniform(1.0, 3.5), 1)
        hi = round(lo + rng.uniform(0.3, 1.5), 1)
        raw = "%g-%g万/月" % (lo, hi)
    elif unit == "元/天":
        lo = float(rng.randint(150, 400))
        hi = lo + float(rng.randint(20, 120))
        raw = "%d-%d元/天" % (lo, hi)
    elif unit == "元/月":
        lo = float(rng.randint(5000, 15000))
        hi = lo + float(rng.randint(1000, 5000))
        raw = "%d-%d元/月" % (lo, hi)
    else:  # pragma: no cover - 防御
        return "", None, None, None, months

    if months and months != 12 and unit in ("K", "万"):
        raw = raw + "·%d薪" % months
    return raw, lo, hi, unit, months


def _make_score(rng: random.Random, status: str):
    if status == "applied":
        return round(rng.uniform(42, 96), 1)
    if status == "failed":
        return round(rng.uniform(0, 22), 1)
    # skipped：多数评分很低
    if rng.random() < 0.28:
        return 0.0
    return round(rng.uniform(1, 38), 1)


def _make_text_fields(rng: random.Random, status: str):
    keyword = rng.choice(KEYWORD_POOL) if rng.random() < 0.97 else ""
    reason = ""
    error = ""
    if rng.random() < 0.72:
        if status == "applied":
            reason = rng.choice(APPLY_REASONS)
        elif status == "skipped":
            reason = rng.choice(SKIP_REASONS)
        else:
            reason = rng.choice(FAIL_REASONS)
    if status == "failed":
        error = rng.choice(FAIL_ERRORS)
    return keyword, reason, error


def build_demo_db(
    out_path: Path = DEFAULT_OUT,
    rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
    end_date: date | None = None,
    span_days: int = DEFAULT_SPAN_DAYS,
) -> dict:
    """生成 demo 数据库，返回统计信息 dict（用于 CLI 打印与测试断言）。"""
    out_path = Path(out_path)
    if rows < span_days:
        raise ValueError("rows 不能小于 span_days（每天至少 1 行）")
    if end_date is None:
        end_date = date.today()
    if isinstance(end_date, datetime):
        end_date = end_date.date()

    rng = random.Random(seed)
    day_counts = _allocate_days(rng, rows, span_days, end_date)

    records = []
    span_start = end_date - timedelta(days=span_days - 1)
    for offset, count in enumerate(day_counts):
        day = span_start + timedelta(days=offset)
        for _ in range(count):
            status = _weighted(rng, STATUS_WEIGHTS)
            source = _weighted(rng, SOURCE_WEIGHTS)
            city = _weighted(rng, list(zip(CITY_POOL, CITY_WEIGHTS)))
            company = rng.choice(COMPANY_POOL)
            job = rng.choice(JOB_POOL)
            salary_raw, min_k, max_k, unit, months = _make_salary(rng, status)
            score = _make_score(rng, status)
            keyword, reason, error = _make_text_fields(rng, status)
            hour = rng.randint(9, 22)
            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            micro = rng.randint(0, 999999)
            ts = "%sT%02d:%02d:%02d.%06d" % (day.isoformat(), hour, minute, second, micro)
            records.append((
                source, status, city, company, job,
                salary_raw, min_k, max_k, unit, months,
                score, keyword, reason, error, ts,
            ))

    # 按时间升序写入（与真实库「按投递时间追加」的顺序一致）
    records.sort(key=lambda r: r[14])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=out_path.name + ".", suffix=".tmp", dir=str(out_path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        conn = sqlite3.connect(str(tmp_path))
        try:
            conn.execute(CREATE_APPLICATIONS_SQL)
            conn.execute(CREATE_META_SQL)
            conn.executemany(
                """INSERT INTO applications
                   (source, status, city, company, job,
                    salary_raw, salary_min_k, salary_max_k, salary_unit, salary_months,
                    score, keyword, reason, error, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                records,
            )
            meta = {
                "generator": "scripts/make_demo_db.py",
                "seed": str(seed),
                "rows": str(rows),
                "span_days": str(span_days),
                "start_date": span_start.isoformat(),
                "end_date": end_date.isoformat(),
                "note": "合成脱敏 demo 数据：公司/岗位/关键词均为自造词表，与真实求职记录无关",
            }
            conn.executemany("INSERT INTO demo_meta (key, value) VALUES (?, ?)", list(meta.items()))
            conn.commit()
        finally:
            conn.close()
        os.replace(str(tmp_path), str(out_path))
        os.chmod(str(out_path), 0o644)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    by_status = {}
    by_source = {}
    for r in records:
        by_status[r[1]] = by_status.get(r[1], 0) + 1
        by_source[r[0]] = by_source.get(r[0], 0) + 1

    return {
        "path": str(out_path),
        "rows": len(records),
        "seed": seed,
        "span_days": span_days,
        "start_date": span_start.isoformat(),
        "end_date": end_date.isoformat(),
        "by_status": by_status,
        "by_source": by_source,
        "size_bytes": out_path.stat().st_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成合成脱敏 demo 数据库（demo/jobintel_demo.db）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出路径（默认 demo/jobintel_demo.db）")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help="行数（默认 1200）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子（默认固定值，保证可复现）")
    parser.add_argument("--span-days", type=int, default=DEFAULT_SPAN_DAYS, help="时间跨度（天，默认 90）")
    parser.add_argument("--end-date", default="", help="最后一天 YYYY-MM-DD（默认今天，保证近 7 天有数据）")
    args = parser.parse_args(argv)

    end_date = None
    if args.end_date:
        try:
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--end-date 需要 YYYY-MM-DD 格式，例如 2026-09-30")

    stats = build_demo_db(
        out_path=Path(args.out),
        rows=args.rows,
        seed=args.seed,
        end_date=end_date,
        span_days=args.span_days,
    )
    print("✅ demo 数据库已生成：%s" % stats["path"])
    print("   行数：%d（%s）" % (stats["rows"], " ".join("%s=%d" % kv for kv in sorted(stats["by_status"].items()))))
    print("   来源：%s" % " ".join("%s=%d" % kv for kv in sorted(stats["by_source"].items())))
    print("   时间跨度：%s → %s（%d 天）" % (stats["start_date"], stats["end_date"], stats["span_days"]))
    print("   文件大小：%.1f KB（seed=%d）" % (stats["size_bytes"] / 1024, stats["seed"]))
    print("   数据全部为合成脱敏数据，与真实求职记录无关。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
