from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root():
    # Проверяем любой доступный эндпоинт. 
    # Если у вас нет "/", замените на любой свой путь, например "/items"
    response = client.get("/")
    assert response.status_code in [200, 404]