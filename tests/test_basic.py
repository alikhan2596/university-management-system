import os

os.environ["SECRET_KEY"] = "ci-test-secret-key"
os.environ["SESSION_MINUTES"] = "30"

from app import app


def test_login_page_loads():
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/login")

        assert response.status_code == 200
        assert b"University Management System" in response.data


def test_dashboard_requires_login():
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/", follow_redirects=False)

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_invalid_route_returns_404():
    app.config["TESTING"] = True

    with app.test_client() as client:
        response = client.get("/this-page-does-not-exist")

        assert response.status_code == 404
