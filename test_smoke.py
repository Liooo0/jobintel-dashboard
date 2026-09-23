"""冒烟测试：用临时 SQLite 验证各 API 可用。

CI 上没有真实 jobintel.db（3 万条投递记录），因此用最小表结构 + 小数据集
验证 overview / trend / detail / insights / 首页入口都能正常返回。
"""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

# 必须在 import app 之前设置数据源（app 模块加载时会检查 DB 存在）
_fd, _tmp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["JOBINTEL_DB"] = _tmp

_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_recent = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
_old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

_conn = sqlite3.connect(_tmp)
_conn.execute("""
CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    source TEXT, status TEXT, city TEXT,
    company TEXT, job TEXT,
    salary_raw TEXT, salary_min_k REAL, salary_max_k REAL,
    salary_unit TEXT, salary_months REAL,
    score REAL, keyword TEXT, reason TEXT, error TEXT, ts TEXT
)
""")
# applied 5 条(2026-08-01 深圳15K无分 / 深圳80 / 广州65 / 北京90 / 深圳75)
# skipped 2 条(深圳 FastAPI 30 / 广州 AI 无分)
# failed 1 条
_conn.executemany(
    "INSERT INTO applications (source, status, city, company, job, keyword, salary_min_k, salary_unit, score, reason, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
    [
        ("boss", "applied", "深圳", "公司A", "Python工程师", "Python", 15.0, "K", None, "", _recent),
        ("51job", "skipped", "广州", "公司B", "AI应用工程师", "AI", 18.0, "K", None, "", _recent),
        ("boss", "applied", "深圳", "公司C", "数据工程师", "Python", 18.0, "K", 80, "", _now),
        ("boss", "applied", "广州", "公司D", "AI产品", "ai", 18.0, "K", 65, "", _now),
        ("boss", "applied", "北京", "公司E", "Agent开发", "ai", 22.0, "K", 90, "", _old),
        ("boss", "applied", "深圳", "公司F", "AI运营", "ai", 20.0, "K", 75, "", _now),
        ("boss", "skipped", "深圳", "公司G", "后端", "python", 9.0, "K", 30, "JD含排除词'FastAPI'→过滤", _now),
        ("boss", "failed", "上海", "公司H", "测试岗", "无", None, None, None, "", _now),
    ],
)
_conn.commit()
_conn.close()

from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


def test_overview():
    r = client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 8
    assert len(data["status"]) == 3
    assert len(data["sources"]) == 2


def test_trend():
    r = client.get("/api/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list) or isinstance(r.json(), dict)


def test_detail():
    r = client.get("/api/detail", params={"city": "深圳"})
    assert r.status_code == 200
    assert r.json() is not None


def test_insights():
    r = client.get("/api/insights")
    assert r.status_code == 200
    d = r.json()
    # 跳过归因: 唯一带 reason 的 skipped 行
    assert d["skip_reasons"] == [{"reason": "JD含排除词'FastAPI'→过滤", "c": 1}]
    # 投递质量: applied 5 条, score>=70 有 80/90/75 = 3 高
    f = d["fit"]
    assert f["high"] == 3 and f["mid"] == 1 and f["low"] == 1 and f["applied_total"] == 5
    # 薪资中位: applied 15/18/18/20/22 → 中位 18
    assert d["median_salary_k"] == 18
    # 高分城市 top1 深圳: 3 条 applied, avg = (80+75)/2 = 77.5
    assert d["top_cities"][0]["city"] == "深圳" and d["top_cities"][0]["c"] == 3
    assert d["top_cities"][0]["avg_score"] == 77.5
    # 质量趋势: 近 30 天 applied(60 天前那条被排除)
    assert len(d["quality_trend"]) >= 1


def test_index():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]