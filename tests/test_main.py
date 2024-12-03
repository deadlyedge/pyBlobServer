import pytest
import io
import requests

@pytest.fixture
def test_user():
    return "luzheng@live.com"

@pytest.fixture
def test_token():
    return "5209cf61-50d1-4593-ac16-600fe1105a9f"

@pytest.fixture
def test_file():
    return {
        "content": b"test file content",
        "filename": "test.txt"
    }

def test_root():
    response = requests.get("http://localhost:8000/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data

def test_get_user(monkeypatch, test_user):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', [test_user])
    response = requests.get(f"http://localhost:8000/user/{test_user}")
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert "user" in data
    assert data["user"] == test_user

def test_get_user_unauthorized(monkeypatch):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', ["authorized_user"])
    response = requests.get("http://localhost:8000/user/unauthorized_user")
    assert response.status_code == 403

def test_upload_file(monkeypatch, test_user, test_token, test_file):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', [test_user])
    monkeypatch.setattr('app.models.ENV.FILE_SIZE_LIMIT_MB', 1)
    monkeypatch.setattr('app.models.ENV.TOTAL_SIZE_LIMIT_MB', 10)
    files = {"file": (test_file["filename"], io.BytesIO(test_file["content"]), "text/plain")}
    headers = {"Authorization": f"Bearer {test_token}"}
    response = requests.post(
        "http://localhost:8000/upload",
        files=files,
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert "file_id" in data
    assert "file_url" in data
    # assert data["file_url"] == test_file["filename"]

def test_list_files(monkeypatch, test_user, test_token):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', [test_user])
    headers = {"Authorization": f"Bearer {test_token}"}
    response = requests.get(
        "http://localhost:8000/list",
        headers=headers
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "file_id" in data[0]
        assert "file_name" in data[0]
        assert "file_size" in data[0]

def test_get_file(monkeypatch, test_user, test_token, test_file):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', [test_user])
    files = {"file": (test_file["filename"], io.BytesIO(test_file["content"]), "text/plain")}
    headers = {"Authorization": f"Bearer {test_token}"}
    upload_response = requests.post(
        "http://localhost:8000/upload",
        files=files,
        headers=headers
    )
    file_id = upload_response.json()["file_id"]
    response = requests.get(
        f"http://localhost:8000/s/{file_id}",
        headers=headers
    )
    assert response.status_code == 200
    assert response.content == test_file["content"]

def test_delete_file(monkeypatch, test_user, test_token, test_file):
    monkeypatch.setattr('app.models.ENV.ALLOWED_USERS', [test_user])
    files = {"file": (test_file["filename"], io.BytesIO(test_file["content"]), "text/plain")}
    headers = {"Authorization": f"Bearer {test_token}"}
    upload_response = requests.post(
        "http://localhost:8000/upload",
        files=files,
        headers=headers
    )
    file_id = upload_response.json()["file_id"]
    response = requests.delete(
        f"http://localhost:8000/delete/{file_id}",
        headers=headers
    )
    assert response.status_code == 200
    assert response.json()["message"] == "File deleted"

def test_delete_all(monkeypatch, test_token ):
    headers = {"Authorization": f"Bearer {test_token}"}
    response = requests.delete(
        "http://localhost:8000/delete_all/?confirm=yes",
        headers=headers
    )
    assert response.status_code == 200
    assert response.json()["message"] == "All files deleted"


# def test_health():
#     response = requests.get("http://localhost:8000/health")
#     assert response.status_code == 200
#     assert response.json() == {"status": "ok"}
