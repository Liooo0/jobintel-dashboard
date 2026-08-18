"""冒烟测试：用临时 SQLite 验证各 API 可用。

CI 上没有真实 jobintel.db（3 万条投递记录），因此用最小表结构 + 2 行数据
验证 overview / trend / detail / 首页四个入口都能正常返回。
"""
import os
import sqlite3
import tempfile

# 必须在 import app 之前设置数据源（app 模块加载时会检查 DB 存在）
_fd, _tmp = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["JOBINTEL_DB"] = _tmp

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
_conn.executemany(
    "INSERT INTO applications (source, status, city, company, job, keyword, salary_min_k, salary_unit, ts) VALUES (?,?,?,?,?,?,?,?,?)",
    [
        ("boss", "applied", "深圳", "测试公司A", "Python工程师", "Python", 15.0, "K", "2026-08-01"),
        ("51job", "skipped", "广州", "测试公司B", "AI应用工程师", "AI", 18.0, "K", "2026-08-02"),
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
    assert data["total"] == 2
    assert len(data["status"]) == 2
    assert len(data["sources"]) == 2


def test_trend():
    r = client.get("/api/trend")
    assert r.status_code == 200
    assert isinstance(r.json(), list) or isinstance(r.json(), dict)


def test_detail():
    r = client.get("/api/detail", params={"city": "深圳"})
    assert r.status_code == 200
    assert r.json() is not None


def test_index():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]