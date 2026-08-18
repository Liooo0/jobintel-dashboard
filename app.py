"""求职数据仪表盘：FastAPI 只读查询 jobintel.db（31k+ 条真实投递记录）。

API:
- GET /api/overview   总览：总量/状态/来源/城市/关键词/薪资分布/近7天投递
- GET /api/trend      趋势：按天投递量（来源拆分 + applied 数）
- GET /api/detail     明细：分页 + 城市/来源/状态/关键词筛选
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"
DB_PATH = Path(os.environ.get("JOBINTEL_DB", Path(__file__).parent.parent / "jobintel" / "data" / "jobintel.db"))
if not DB_PATH.exists():
    raise RuntimeError(f"jobintel.db 不存在: {DB_PATH}（可用环境变量 JOBINTEL_DB 指定）")

app = FastAPI(title="求职数据仪表盘")


def query(sql: str, params: tuple = ()) -> list[dict]:
    """只读查询，每次短连接（与 llm-arena 同款风格）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@app.get("/api/overview")
def api_overview() -> dict:
    total = query("SELECT COUNT(*) c FROM applications")[0]["c"]
    status = query("SELECT status, COUNT(*) c FROM applications GROUP BY status ORDER BY c DESC")
    sources = query("SELECT source, COUNT(*) c FROM applications GROUP BY source ORDER BY c DESC")
    cities = query(
        "SELECT city, COUNT(*) c FROM applications WHERE city != '' GROUP BY city ORDER BY c DESC LIMIT 10"
    )
    keywords = query(
        "SELECT keyword, COUNT(*) c FROM applications WHERE keyword != '' GROUP BY keyword ORDER BY c DESC LIMIT 10"
    )
    salary = query(
        """SELECT CASE salary_unit
                WHEN '万' THEN salary_min_k * 10
                WHEN '元/月' THEN salary_min_k / 1000.0
                WHEN '元/天' THEN salary_min_k * 30 / 1000.0
                ELSE salary_min_k END AS mk
           FROM applications
           WHERE salary_min_k IS NOT NULL AND salary_min_k > 0"""
    )
    edges = [(0, 5), (5, 8), (8, 10), (10, 15), (15, 20), (20, 30), (30, 1e9)]
    labels = ["<5k", "5-8k", "8-10k", "10-15k", "15-20k", "20-30k", "30k+"]
    bins = [0] * len(edges)
    for r in salary:
        mk = r["mk"]
        for i, (lo, hi) in enumerate(edges):
            if lo <= mk < hi:
                bins[i] += 1
                break
    week = query(
        """SELECT substr(ts,1,10) d, COUNT(*) c
           FROM applications
           WHERE ts != '' AND status='applied' AND ts >= datetime('now','-7 days')
           GROUP BY d ORDER BY d"""
    )
    return {
        "total": total,
        "status": status,
        "sources": sources,
        "cities": cities,
        "keywords": keywords,
        "salary": {"labels": labels, "bins": bins},
        "week": week,
    }


@app.get("/api/trend")
def api_trend(days: int = 30) -> dict:
    rows = query(
        """SELECT substr(ts,1,10) d,
                  SUM(CASE WHEN source='boss' THEN 1 ELSE 0 END) boss,
                  SUM(CASE WHEN source='51job' THEN 1 ELSE 0 END) j51,
                  SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) applied
           FROM applications WHERE ts != ''
           GROUP BY d ORDER BY d DESC LIMIT ?""",
        (days,),
    )
    return {"days": list(reversed(rows))}


@app.get("/api/insights")
def api_insights() -> dict:
    """决策洞察：从投递记录里回答「哪些值得投、哪些被规则误伤、投的质量如何」。

    全部只读查询。数据源 applications 表的 status 仅 skipped/applied/failed，
    因此这里是投递决策层(选岗质量)而非结果漏斗层(回复率需接 job-hunter 的失败追踪库)。
    """
    # 跳过归因：什么规则砍掉了最多投递(排除词 / 缺关键词 / 公司名过滤…)
    skip = query(
        """SELECT reason, COUNT(*) c FROM applications
           WHERE reason IS NOT NULL AND reason != '' AND status = 'skipped'
           GROUP BY reason ORDER BY c DESC LIMIT 12"""
    )
    # 投递质量：applied 的匹配分三段(排除词归零前按 JD 匹配计分)
    fit = query(
        """SELECT
             SUM(CASE WHEN score >= 70 THEN 1 ELSE 0 END) AS high,
             SUM(CASE WHEN score >= 40 AND score < 70 THEN 1 ELSE 0 END) AS mid,
             SUM(CASE WHEN score < 40 OR score IS NULL THEN 1 ELSE 0 END) AS low,
             ROUND(AVG(score), 1) AS avg_score
           FROM applications WHERE status = 'applied'"""
    )[0]
    applied_total = query("SELECT COUNT(*) c FROM applications WHERE status='applied'")[0]["c"]
    # 投递目标薪资中位数(月度 K)
    salary_rows = query(
        """SELECT salary_min_k AS mk FROM applications
           WHERE status = 'applied' AND salary_min_k IS NOT NULL AND salary_min_k > 0
           ORDER BY salary_min_k"""
    )
    median_k = salary_rows[len(salary_rows) // 2]["mk"] if salary_rows else None
    # 高分机会在哪：城市 × 平均匹配分(投递量 top6)
    cities = query(
        """SELECT city, COUNT(*) c, ROUND(AVG(score), 1) AS avg_score
           FROM applications WHERE status = 'applied' AND city != ''
           GROUP BY city ORDER BY c DESC LIMIT 6"""
    )
    keywords = query(
        """SELECT keyword, COUNT(*) c, ROUND(AVG(score), 1) AS avg_score
           FROM applications WHERE status = 'applied' AND keyword != ''
           GROUP BY keyword ORDER BY c DESC LIMIT 6"""
    )
    # 30 天质量趋势：每天平均匹配分
    quality = query(
        """SELECT substr(ts, 1, 10) d, COUNT(*) c, ROUND(AVG(score), 1) AS avg_score
           FROM applications
           WHERE status = 'applied' AND ts != '' AND ts >= datetime('now', '-30 days')
           GROUP BY d ORDER BY d"""
    )
    return {
        "skip_reasons": skip,
        "fit": {**fit, "applied_total": applied_total},
        "median_salary_k": median_k,
        "top_cities": cities,
        "top_keywords": keywords,
        "quality_trend": quality,
    }
@app.get("/api/detail")
def api_detail(
    page: int = 1, page_size: int = 50,
    city: str = "", source: str = "", status: str = "", kw: str = "",
) -> dict:
    where, args = [], []
    if city:
        where.append("city = ?")
        args.append(city)
    if source:
        where.append("source = ?")
        args.append(source)
    if status:
        where.append("status = ?")
        args.append(status)
    if kw:
        where.append("(job LIKE ? OR keyword LIKE ? OR company LIKE ?)")
        args += [f"%{kw}%"] * 3
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = query(f"SELECT COUNT(*) c FROM applications {w}", args)[0]["c"]
    items = query(
        f"""SELECT ts, source, status, city, company, job, salary_raw, score, keyword, reason
            FROM applications {w} ORDER BY ts DESC LIMIT ? OFFSET ?""",
        args + [page_size, (page - 1) * page_size],
    )
    return {"total": total, "page": page, "page_size": page_size, "items": items}


# ---------------------------------------------------------------------------
# 静态页面
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def page_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/trend", include_in_schema=False)
def page_trend() -> FileResponse:
    return FileResponse(STATIC_DIR / "trend.html")


@app.get("/insights", include_in_schema=False)
def page_insights() -> FileResponse:
    return FileResponse(STATIC_DIR / "insights.html")


@app.get("/detail", include_in_schema=False)
def page_detail() -> FileResponse:
    return FileResponse(STATIC_DIR / "detail.html")
