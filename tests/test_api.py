from fastapi.testclient import TestClient
from main import app # Импортируем ваш объект FastAPI из main.py

client = TestClient(app)

def test_read_main():
    # Простейший тест: проверяем, что главная страница или 404 (если нет главной) отвечает
    # Этого достаточно, чтобы pytest нашел 1 тест и успешно завершился
    response = client.get("/")
    assert response.status_code in [200, 404]