def test_health_reports_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_data_survives_app_restart(data_dir):
    from starlette.testclient import TestClient
    from finance.web import create_app

    plan = {"version": 1, "income": None, "bills": [], "monthly_spend": {}, "buffer": 150, "goals": []}
    with TestClient(create_app()) as c:
        assert c.put("/api/plan", json=plan).status_code == 200
    with TestClient(create_app()) as c:
        assert c.get("/api/plan").json()["buffer"] == 150
