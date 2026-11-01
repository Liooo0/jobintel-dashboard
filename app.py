"""求职数据仪表盘：FastAPI 只读查询 jobintel.db。

API:
- GET /api/overview   总览：总量/状态/来源/城市/关键词/薪资分布/近7天投递
- GET /api/trend      趋势：按天投递量（来源拆分 + applied 数）
- GET /api/detail     明细：分页 + 城市/来源/状态/关键词筛选
- GET /api/healthz    健康检查：ok + 实际使用的 DB 路径 + 数据来源（env/local/demo）

数据库解析顺序
--------------
1. 环境变量 ``JOBINTEL_DB`` 指向的文件（显式指定；文件不存在直接报错，不静默降级）；
2. 本机真实库 ``../jobintel/data/jobintel.db``（开发者机器上沿用旧路径，source=local）；
3. 仓库内置合成 demo 库 ``demo/jobintel_demo.db``（外部 clone 没有第 2 条路径，自动落到这里）；
4. 都没有：抛出带下一步指引的 RuntimeError。
生成 demo 数据：``python3 scripts/make_demo_db.py``。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEMO_DB_PATH = BASE_DIR / "demo" / "jobintel_demo.db"
# 开发者机器上的真实库（旧版默认路径）；公开 clone 里不存在
LEGACY_DB_PATH = BASE_DIR.parent / "jobintel" / "data" / "jobintel.db"


def resolve_db_path(
    env: Mapping[str, str] | None = None,
    demo_path: Path | None = None,
    legacy_path: Path | None = None,
) -> tuple[Path, str]:
    """按 JOBINTEL_DB → 本机真实库 → demo 库 → 报错 的顺序解析数据库路径。

    返回 ``(数据库路径, 来源)``，来源取值 ``"env"`` / ``"local"`` / ``"demo"``。
    ``demo_path`` / ``legacy_path`` 仅用于测试替换对应位置。
    """
    env = os.environ if env is None else env
    demo = Path(demo_path) if demo_path is not None else DEMO_DB_PATH
    legacy = Path(legacy_path) if legacy_path is not None else LEGACY_DB_PATH

    explicit = (env.get("JOBINTEL_DB") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            # 显式指定却找不到：直接报错，不静默降级到 local/demo
            raise RuntimeError(
                "JOBINTEL_DB 指向的数据库不存在: %s\n"
                "请检查路径，或去掉 JOBINTEL_DB 使用本机真实库/内置 demo 数据。" % path
            )
        return path, "env"

    if legacy.exists():
        return legacy, "local"

    if demo.exists():
        return demo, "demo"

    raise RuntimeError(
        "找不到数据库：未设置环境变量 JOBINTEL_DB，本机真实库与内置 demo 库均不存在"
        "（检查过 %s 和 %s）。\n"
        "  · 生成合成 demo 数据后直接启动：python3 scripts/make_demo_db.py\n"
        "  · 或指向真实库：JOBINTEL_DB=/path/to/jobintel.db uvicorn app:app --port 8001"
        % (legacy, demo)
    )


DB_PATH, DB_SOURCE = resolve_db_path()

if DB_SOURCE == "local":
    print(
        "[jobintel-dashboard] 使用本机真实数据：%s" % DB_PATH,
        file=sys.stderr,
    )
elif DB_SOURCE == "demo":
    print(
        "[jobintel-dashboard] 未找到本机真实库，使用合成 demo 数据：%s\n"
        "                    真实数据请设置 JOBINTEL_DB=/path/to/jobintel.db" % DB_PATH,
        file=sys.stderr,
    )

app = FastAPI(title="求职数据仪表盘")


def query(sql: str, params: tuple = ()) -> list[dict]:
    """只读查询，每次短连接（与 llm-arena 同款风格）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@app.get("/api/healthz")
def api_healthz() -> dict:
    """健康检查：确认进程活着且数据库可读。"""
    try:
        rows = query("SELECT COUNT(*) c FROM applications")[0]["c"]
    except sqlite3.Error as exc:  # pragma: no cover - 损坏库才会走到
        raise HTTPException(status_code=503, detail="数据库不可读: %s" % exc) from exc
    return {"ok": True, "db": str(DB_PATH), "source": DB_SOURCE, "rows": rows}


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


@app.get("/detail", include_in_schema=False)
def page_detail() -> FileResponse:
    return FileResponse(STATIC_DIR / "detail.html")
