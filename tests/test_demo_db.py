"""demo 数据与「clone 后 3 分钟可用」验收测试。

覆盖：
- demo/jobintel_demo.db 存在、schema 与真实表一致、约 1200 行、时间跨度合理；
- 所有公司/岗位/城市/关键词都能在本项目的合成词表里找到（即：没有混入真实数据）；
- 固定 seed + end_date 时生成结果可复现（CLI 重新生成后逐行一致）；
- app 的 DB 解析顺序：JOBINTEL_DB → demo 库 → 带指引的报错；
- app 连 demo 库时 /api/overview /api/trend /api/detail / /api/healthz / 首页全部 200 且有数据。
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
DEMO_DB = ROOT / "demo" / "jobintel_demo.db"
GENERATOR = ROOT / "scripts" / "make_demo_db.py"

# 真实库 applications 表的字段（顺序与类型），demo 库必须一致
EXPECTED_COLUMNS = [
    ("id", "INTEGER"),
    ("source", "TEXT"),
    ("status", "TEXT"),
    ("city", "TEXT"),
    ("company", "TEXT"),
    ("job", "TEXT"),
    ("salary_raw", "TEXT"),
    ("salary_min_k", "REAL"),
    ("salary_max_k", "REAL"),
    ("salary_unit", "TEXT"),
    ("salary_months", "INTEGER"),
    ("score", "REAL"),
    ("keyword", "TEXT"),
    ("reason", "TEXT"),
    ("error", "TEXT"),
    ("ts", "TEXT"),
]


def _load_generator():
    """以文件路径加载生成器，复用其中的合成词表做断言。"""
    spec = importlib.util.spec_from_file_location("make_demo_db", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()


def _read_meta(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        return dict(conn.execute("SELECT key, value FROM demo_meta").fetchall())
    finally:
        conn.close()


def _read_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT * FROM applications ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def demo_rows() -> list[tuple]:
    assert DEMO_DB.exists(), "缺少 demo/jobintel_demo.db，先运行 python3 scripts/make_demo_db.py"
    return _read_rows(DEMO_DB)


@pytest.fixture(scope="module")
def demo_meta() -> dict:
    assert DEMO_DB.exists(), "缺少 demo/jobintel_demo.db，先运行 python3 scripts/make_demo_db.py"
    return _read_meta(DEMO_DB)


# ---------------------------------------------------------------------------
# demo 数据库本身
# ---------------------------------------------------------------------------
def test_demo_db_file_exists_and_sized():
    assert DEMO_DB.is_file()
    size = DEMO_DB.stat().st_size
    # 约 1200 行的 SQLite 文件，几百 KB
    assert 50 * 1024 < size < 5 * 1024 * 1024, "demo DB 大小异常: %d bytes" % size


def test_schema_matches_real_table():
    conn = sqlite3.connect(str(DEMO_DB))
    try:
        cols = [(r[1], r[2].upper()) for r in conn.execute("PRAGMA table_info(applications)")]
        pk = [r for r in conn.execute("PRAGMA table_info(applications)") if r[5]]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    assert cols == EXPECTED_COLUMNS
    assert pk and pk[0][1] == "id"
    assert {"applications", "demo_meta"} <= tables


def test_row_count_and_meta(demo_rows, demo_meta):
    assert demo_meta["generator"] == "scripts/make_demo_db.py"
    assert len(demo_rows) == int(demo_meta["rows"]) == generator.DEFAULT_ROWS
    assert len(demo_rows) == 1200


def test_time_span(demo_rows, demo_meta):
    ts_values = [r[-1] for r in demo_rows]
    assert all(len(ts) == 26 and ts[10] == "T" for ts in ts_values), "ts 应为 ISO 时间戳（与真实库一致）"

    dates = sorted({ts[:10] for ts in ts_values})
    start, end = dates[0], dates[-1]
    assert start == demo_meta["start_date"]
    assert end == demo_meta["end_date"]

    from datetime import date

    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    assert span == int(demo_meta["span_days"]) == generator.DEFAULT_SPAN_DAYS
    assert 60 <= span <= 120, "时间跨度应接近真实库的三个月量级，实际 %d 天" % span


def test_all_values_are_synthetic(demo_rows):
    """所有文本字段都必须落在本项目自造的合成词表里（没有混入真实数据）。"""
    companies = {r[4] for r in demo_rows}
    jobs = {r[5] for r in demo_rows}
    cities = {r[3] for r in demo_rows}
    keywords = {r[12] for r in demo_rows}
    statuses = {r[2] for r in demo_rows}
    sources = {r[1] for r in demo_rows}
    reasons = {r[13] for r in demo_rows}
    errors = {r[14] for r in demo_rows}
    units = {r[9] for r in demo_rows}

    assert companies <= set(generator.COMPANY_POOL)
    assert jobs <= set(generator.JOB_POOL)
    assert cities <= set(generator.CITY_POOL)
    assert keywords <= set(generator.KEYWORD_POOL) | {""}
    assert statuses <= {"applied", "skipped", "failed"}
    assert sources <= {"boss", "51job", "liepin"}
    assert reasons <= (
        set(generator.SKIP_REASONS) | set(generator.APPLY_REASONS)
        | set(generator.FAIL_REASONS) | {""}
    )
    assert errors <= set(generator.FAIL_ERRORS) | {""}
    assert units <= {"K", "万", "元/天", "元/月", None}

    # 错误信息只出现在 failed 记录上（与真实库一致）
    for row in demo_rows:
        if row[14]:
            assert row[2] == "failed"


def test_distribution_is_reasonable(demo_rows):
    total = len(demo_rows)
    by_status = {}
    by_source = {}
    for r in demo_rows:
        by_status[r[2]] = by_status.get(r[2], 0) + 1
        by_source[r[1]] = by_source.get(r[1], 0) + 1

    skipped_ratio = by_status.get("skipped", 0) / total
    applied_ratio = by_status.get("applied", 0) / total
    # 真实库约 82% skipped / 16.5% applied，允许区间
    assert 0.65 <= skipped_ratio <= 0.95
    assert 0.05 <= applied_ratio <= 0.30
    # boss 为绝对主来源
    assert by_source.get("boss", 0) / total >= 0.80


def test_generation_is_reproducible(tmp_path, demo_meta, demo_rows):
    """同一 seed + end_date 重新生成，逐行一致（固定随机种子可复现）。"""
    out = tmp_path / "regen.db"
    proc = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--out", str(out),
            "--rows", demo_meta["rows"],
            "--seed", demo_meta["seed"],
            "--span-days", demo_meta["span_days"],
            "--end-date", demo_meta["end_date"],
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    assert _read_rows(out) == demo_rows
    assert _read_meta(out) == demo_meta
    assert out.stat().st_size == DEMO_DB.stat().st_size


# ---------------------------------------------------------------------------
# app 的 DB 解析顺序
# ---------------------------------------------------------------------------
def test_resolve_prefers_env(tmp_path):
    """JOBINTEL_DB 优先级最高：即使本机真实库存在也用 env。"""
    import app as app_module

    explicit = tmp_path / "mine.db"
    explicit.write_bytes(b"")
    legacy = tmp_path / "jobintel" / "data" / "jobintel.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"")

    path, source = app_module.resolve_db_path(
        env={"JOBINTEL_DB": str(explicit)}, legacy_path=legacy
    )
    assert source == "env"
    assert path == explicit


def test_resolve_prefers_legacy_over_demo(tmp_path):
    """本机真实库（旧路径）优先于内置 demo，保持本机旧行为不变。"""
    import app as app_module

    legacy = tmp_path / "jobintel" / "data" / "jobintel.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"")

    path, source = app_module.resolve_db_path(
        env={}, demo_path=DEMO_DB, legacy_path=legacy
    )
    assert source == "local"
    assert path == legacy


def test_legacy_default_path_matches_old_layout():
    """默认 legacy 路径仍是旧版 README 里写的 ../jobintel/data/jobintel.db。"""
    import app as app_module

    assert app_module.LEGACY_DB_PATH == (
        app_module.BASE_DIR.parent / "jobintel" / "data" / "jobintel.db"
    )


def test_resolve_falls_back_to_demo_when_no_legacy(tmp_path):
    """外部 clone 没有 legacy 路径时落到内置 demo 库。"""
    import app as app_module

    legacy = tmp_path / "no-jobintel" / "data" / "jobintel.db"
    path, source = app_module.resolve_db_path(
        env={}, demo_path=DEMO_DB, legacy_path=legacy
    )
    assert source == "demo"
    assert path == app_module.DEMO_DB_PATH == DEMO_DB


def test_resolve_missing_db_raises_with_hint(tmp_path):
    import app as app_module

    missing_demo = tmp_path / "nope" / "jobintel_demo.db"
    missing_legacy = tmp_path / "nope" / "jobintel" / "data" / "jobintel.db"
    with pytest.raises(RuntimeError) as exc_info:
        app_module.resolve_db_path(
            env={}, demo_path=missing_demo, legacy_path=missing_legacy
        )
    message = str(exc_info.value)
    assert "make_demo_db.py" in message
    assert "JOBINTEL_DB" in message


def test_resolve_env_points_to_missing_file(tmp_path):
    import app as app_module

    with pytest.raises(RuntimeError):
        app_module.resolve_db_path(env={"JOBINTEL_DB": str(tmp_path / "absent.db")})


# ---------------------------------------------------------------------------
# app 连 demo 库时的接口
# ---------------------------------------------------------------------------
@pytest.fixture()
def demo_client(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", DEMO_DB)
    monkeypatch.setattr(app_module, "DB_SOURCE", "demo")
    with TestClient(app_module.app) as client:
        yield client


def test_healthz(demo_client):
    r = demo_client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["source"] == "demo"
    assert body["db"].endswith("demo/jobintel_demo.db")
    assert body["rows"] == 1200


def test_overview_has_data(demo_client):
    r = demo_client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1200
    assert data["status"] and data["sources"] and data["cities"] and data["keywords"]
    assert set(data["salary"].keys()) == {"labels", "bins"}
    assert len(data["salary"]["bins"]) == len(data["salary"]["labels"]) == 7
    assert sum(data["salary"]["bins"]) > 0
    assert sum(data["salary"]["bins"]) <= data["total"]
    assert isinstance(data["week"], list)


def test_trend_has_data(demo_client):
    r = demo_client.get("/api/trend", params={"days": 30})
    assert r.status_code == 200
    days = r.json()["days"]
    assert days, "趋势数据不应为空"
    assert len(days) <= 30
    assert {"d", "boss", "j51", "applied"} <= set(days[0].keys())
    assert sum(d["boss"] for d in days) > 0
    assert [d["d"] for d in days] == sorted(d["d"] for d in days)


def test_detail_has_data_and_filters(demo_client):
    r = demo_client.get("/api/detail", params={"page": 1, "page_size": 50})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1200
    assert len(data["items"]) == 50
    assert data["items"][0]["ts"] >= data["items"][-1]["ts"]
    for item in data["items"]:
        assert item["company"] in set(generator.COMPANY_POOL)

    city = data["items"][0]["city"]
    filtered = demo_client.get("/api/detail", params={"city": city, "page_size": 5}).json()
    assert 0 < filtered["total"] < 1200

    kw = demo_client.get("/api/detail", params={"kw": "星辰", "page_size": 5}).json()
    assert kw["total"] > 0


def test_pages_render(demo_client):
    for path in ("/", "/trend", "/detail"):
        r = demo_client.get(path)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"]
        assert "求职数据仪表盘" in r.text


def test_app_imports_without_env_using_demo_db(tmp_path):
    """模拟外部 clone：没有 legacy 真实库、不设环境变量，import app 走内置 demo。"""
    import shutil

    clone = tmp_path / "clone"
    clone.mkdir()
    shutil.copy2(ROOT / "app.py", clone / "app.py")
    shutil.copytree(ROOT / "static", clone / "static")
    shutil.copytree(ROOT / "demo", clone / "demo")

    # 该位置确实没有本机 legacy 真实库
    assert not (clone.parent.parent / "jobintel" / "data" / "jobintel.db").exists()

    env = {k: v for k, v in os.environ.items() if k != "JOBINTEL_DB"}
    proc = subprocess.run(
        [sys.executable, "-c", "import app; print(app.DB_SOURCE); print(app.DB_PATH)"],
        cwd=str(clone),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "demo"
    assert lines[1].endswith("demo/jobintel_demo.db")
    assert "未找到本机真实库" in proc.stderr


def test_healthz_reports_local_source(monkeypatch, tmp_path):
    """source=local 时 healthz 如实报告来源与行数。"""
    import app as app_module

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        for _ in range(3):
            conn.execute("INSERT INTO applications DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(app_module, "DB_PATH", db)
    monkeypatch.setattr(app_module, "DB_SOURCE", "local")
    with TestClient(app_module.app) as client:
        r = client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["source"] == "local"
    assert body["rows"] == 3
