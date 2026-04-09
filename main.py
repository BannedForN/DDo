from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import List, Optional, Any
import os
import hashlib
import threading
import hmac
import base64
import time
import requests
import imghdr
from pathlib import Path
from sqlalchemy import text as sql_text
import zipfile
import re
import io

import models
import schemas
from database import Base, engine, get_db, SessionLocal

app = FastAPI(title="Elion Local API")

MODEL_ENDPOINT = os.getenv("LLM_ENDPOINT", "http://localhost:1234/v1/chat/completions")
MODEL_NAME = os.getenv("LLM_MODEL", "gemma-3-4b-it")
MODEL_API_KEY = os.getenv("LLM_API_KEY")

# Папка для загруженных изображений
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads")).resolve()
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# Внешний базовый URL вашего API, чтобы модель могла скачать картинки.
# Пример: http://192.168.1.10:8000  (или https://... если есть)
PUBLIC_API_BASE_URL = os.getenv("PUBLIC_API_BASE_URL", "http://100.84.92.66:8000").strip().rstrip("/")


def _ensure_url_scheme(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "http://" + u

def _ensure_image_url_column():
    """
    Мини-миграция: добавляем колонку image_url, если её ещё нет.
    """
    try:
        with engine.connect() as conn:
            db_name = conn.execute(sql_text("SELECT DATABASE()")).scalar()
            if not db_name:
                return
            exists = conn.execute(
                sql_text(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = :schema
                      AND TABLE_NAME = 'brenks_essence_android_user_messages'
                      AND COLUMN_NAME = 'image_url'
                    """
                ),
                {"schema": db_name},
            ).scalar()
            if not exists:
                conn.execute(
                    sql_text(
                        "ALTER TABLE brenks_essence_android_user_messages "
                        "ADD COLUMN image_url VARCHAR(512) NULL"
                    )
                )
                conn.commit()
    except Exception:
        return


def _ensure_document_columns():
    """
    Мини-миграция: добавляем колонки для документов, если их ещё нет.
    """
    try:
        with engine.connect() as conn:
            db_name = conn.execute(sql_text("SELECT DATABASE()")).scalar()
            if not db_name:
                return

            def _has(col: str) -> bool:
                return bool(conn.execute(
                    sql_text(
                        """
                        SELECT COUNT(*) FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = :schema
                          AND TABLE_NAME = 'brenks_essence_android_user_messages'
                          AND COLUMN_NAME = :col
                        """
                    ),
                    {"schema": db_name, "col": col},
                ).scalar())

            alters = []
            if not _has("document_url"):
                alters.append("ADD COLUMN document_url VARCHAR(512) NULL")
            if not _has("document_name"):
                alters.append("ADD COLUMN document_name VARCHAR(255) NULL")
            if not _has("document_mime"):
                alters.append("ADD COLUMN document_mime VARCHAR(128) NULL")
            if not _has("document_text"):
                alters.append("ADD COLUMN document_text TEXT NULL")

            if alters:
                conn.execute(sql_text(
                    "ALTER TABLE brenks_essence_android_user_messages " + ", ".join(alters)
                ))
                conn.commit()
    except Exception:
        return


def _ensure_roles_and_modes_tables():
    """Таблицы ролей приложения и режимов ответа."""
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS brenks_essence_android_user_roles (
            id_user INT NOT NULL,
            role VARCHAR(32) NOT NULL,
            PRIMARY KEY (id_user, role)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS brenks_essence_android_response_modes (
            id_mode INT NOT NULL AUTO_INCREMENT,
            template_key VARCHAR(64) NOT NULL,
            title VARCHAR(255) NOT NULL,
            system_prompt TEXT NOT NULL,
            sort_order INT NOT NULL DEFAULT 0,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            date_created DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id_mode),
            UNIQUE KEY uk_response_mode_template (template_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]
    try:
        with engine.connect() as conn:
            for s in stmts:
                conn.execute(sql_text(s))
            conn.commit()
    except Exception:
        return


def _ensure_llm_settings_table():
    """Одна строка настроек генерации (температура, max_tokens и т.д.)."""
    stmt = """
    CREATE TABLE IF NOT EXISTS brenks_essence_android_llm_settings (
        id_settings INT NOT NULL PRIMARY KEY,
        temperature DOUBLE NOT NULL DEFAULT 0.7,
        max_tokens INT NOT NULL DEFAULT 512,
        top_p DOUBLE NULL,
        frequency_penalty DOUBLE NULL,
        presence_penalty DOUBLE NULL,
        repeat_penalty DOUBLE NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    try:
        with engine.connect() as conn:
            conn.execute(sql_text(stmt))
            conn.commit()
    except Exception:
        return


def _seed_llm_settings_if_empty(db: Session) -> None:
    row = db.get(models.AndroidLlmSettings, 1)
    if row is not None:
        return
    db.add(
        models.AndroidLlmSettings(
            id_settings=1,
            temperature=0.7,
            max_tokens=512,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            repeat_penalty=None,
        )
    )
    try:
        db.commit()
    except Exception:
        db.rollback()


def _get_llm_settings_row(db: Session) -> models.AndroidLlmSettings:
    _seed_llm_settings_if_empty(db)
    row = db.get(models.AndroidLlmSettings, 1)
    if row is None:
        return models.AndroidLlmSettings(id_settings=1, temperature=0.7, max_tokens=512)
    return row


def _llm_row_to_schema(row: models.AndroidLlmSettings) -> schemas.LlmSettings:
    return schemas.LlmSettings(
        temperature=float(row.temperature),
        max_tokens=int(row.max_tokens),
        top_p=row.top_p,
        frequency_penalty=row.frequency_penalty,
        presence_penalty=row.presence_penalty,
        repeat_penalty=row.repeat_penalty,
    )


def _apply_llm_row_to_payload(row: models.AndroidLlmSettings, payload: dict) -> None:
    payload["temperature"] = float(row.temperature)
    payload["max_tokens"] = int(row.max_tokens)
    if row.top_p is not None:
        payload["top_p"] = float(row.top_p)
    if row.frequency_penalty is not None:
        payload["frequency_penalty"] = float(row.frequency_penalty)
    if row.presence_penalty is not None:
        payload["presence_penalty"] = float(row.presence_penalty)
    if row.repeat_penalty is not None:
        payload["repeat_penalty"] = float(row.repeat_penalty)


def _user_roles_list(db: Session, user_id: int) -> List[str]:
    rows = (
        db.query(models.AndroidUserAppRole)
        .filter(models.AndroidUserAppRole.id_user == user_id)
        .all()
    )
    return [r.role for r in rows]


def _sync_app_roles_from_env(db: Session):
    """Назначение ролей из .env: PROMPT_ENGINEER_USER_IDS=1,2  ADMIN_USER_IDS=3"""
    def _add(uid: int, role: str):
        exists = (
            db.query(models.AndroidUserAppRole)
            .filter(
                models.AndroidUserAppRole.id_user == uid,
                models.AndroidUserAppRole.role == role,
            )
            .first()
        )
        if not exists:
            db.add(models.AndroidUserAppRole(id_user=uid, role=role))

    for part in os.getenv("PROMPT_ENGINEER_USER_IDS", "").split(","):
        part = part.strip()
        if part.isdigit():
            _add(int(part), "prompt_engineer")
    for part in os.getenv("ADMIN_USER_IDS", "").split(","):
        part = part.strip()
        if part.isdigit():
            _add(int(part), "admin")
    try:
        db.commit()
    except Exception:
        db.rollback()


def _seed_response_modes_if_empty(db: Session):
    if db.query(models.AndroidResponseMode).first() is not None:
        return
    defaults = [
        ("default", "Обычный помощник", "Ты — Элион, дружелюбный помощник.", 0, True),
        (
            "devils_advocate",
            "Адвокат дьявола",
            "Ты — «адвокат дьявола», циничный критик и опытный дебатёр. "
            "Твоя задача — найти слабые места в утверждении пользователя. "
            "Не соглашайся с пользователем. Приводи контраргументы. "
            "Будь вежлив, но твёрд. Найди как минимум 3 причины, "
            "почему идея пользователя может провалиться.",
            10,
            True,
        ),
        (
            "analogy",
            "Генератор аналогий",
            "Ты — генератор аналогий для обучения. Объясняй термины и идеи пользователя "
            "исключительно через аналогии из реальной жизни (еда, машины, животные, быт и т.п.). "
            "Не используй сухие академические определения. "
            "Всегда начинай ответ с фразы: «Представь, что это ...».",
            20,
            True,
        ),
        (
            "commit_message",
            "Commit message",
            "Ты — Senior Developer. Тебе дают diff или описание изменений в коде. "
            "Проанализируй изменения и напиши одно короткое, но ёмкое сообщение для git commit "
            "в формате: [Тип]: Описание. Используй только типы: feat, fix, refactor, docs. "
            "Не пиши ничего, кроме одного commit message.",
            30,
            True,
        ),
        (
            "smm_clickbait",
            "SMM / кликбейт",
            "Ты — SMM-менеджер и креативный копирайтер. Перепиши текст пользователя "
            "для публикации в соцсетях (Twitter/Telegram). Сделай его захватывающим, "
            "добавь эмодзи, хэштеги и призыв к действию. "
            "Сгенерируй ровно 3 варианта: 1) Сдержанный, 2) Весёлый, 3) Агрессивный. "
            "Ясно пометь каждый вариант заголовком и не добавляй ничего лишнего.",
            40,
            True,
        ),
    ]
    for key, title, prompt, order, active in defaults:
        db.add(
            models.AndroidResponseMode(
                template_key=key,
                title=title,
                system_prompt=prompt,
                sort_order=order,
                is_active=active,
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()


def _mode_to_schema(m: models.AndroidResponseMode) -> schemas.ResponseMode:
    return schemas.ResponseMode(
        id=m.id_mode,
        template_key=m.template_key,
        title=m.title,
        system_prompt=m.system_prompt,
        sort_order=m.sort_order,
        is_active=bool(m.is_active),
    )


def _validate_active_template(db: Session, template: Optional[str]) -> Optional[str]:
    if not template or not str(template).strip():
        return None
    t = str(template).strip()
    row = (
        db.query(models.AndroidResponseMode)
        .filter(
            models.AndroidResponseMode.template_key == t,
            models.AndroidResponseMode.is_active == True,  # noqa: E712
        )
        .first()
    )
    return t if row else None


# Ограничения контекста и длины сообщений
MAX_CONTEXT_MESSAGES = int(os.getenv("MAX_CONTEXT_MESSAGES", "30"))
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "4000"))

# Глобальная блокировка для последовательной обработки запросов к LLM
LLM_LOCK = threading.Lock()

# Токены доступа
AUTH_SECRET = os.getenv("AUTH_SECRET", "change_me_in_env")
AUTH_TOKEN_TTL = int(os.getenv("AUTH_TOKEN_TTL", "86400"))  # по умолчанию 1 день
auth_scheme = HTTPBearer(auto_error=True)

# Управление авто-инициализацией БД и тестовых данных
INIT_DB_ON_STARTUP = os.getenv("INIT_DB_ON_STARTUP", "false").lower() == "true"


def _password_hash(plain: str) -> str:
    s = plain
    for _ in range(3):
        s = hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()
    return s


def _create_token(user_id: int) -> str:
    """Простой HMAC-токен: user_id:exp, подписанный секретом, закодирован base64url."""
    exp = int(time.time()) + AUTH_TOKEN_TTL
    payload = f"{user_id}:{exp}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
    raw = payload + b"." + sig
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _verify_token(token: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        payload, sig = raw.rsplit(b".", 1)
        expected_sig = hmac.new(AUTH_SECRET.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected_sig):
            raise ValueError("bad signature")
        user_id_str, exp_str = payload.decode("utf-8").split(":")
        if int(exp_str) < int(time.time()):
            raise ValueError("expired")
        return int(user_id_str)
    except Exception:
        raise HTTPException(status_code=401, detail="Недействительный или просроченный токен")


def get_current_user_id(credentials: HTTPAuthorizationCredentials = Depends(auth_scheme)) -> int:
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Ожидается Bearer токен")
    return _verify_token(credentials.credentials)


def require_prompt_engineer(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> int:
    if "prompt_engineer" not in _user_roles_list(db, user_id):
        raise HTTPException(status_code=403, detail="Доступно только промпт-инженерам")
    return user_id


def require_admin(
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> int:
    if "admin" not in _user_roles_list(db, user_id):
        raise HTTPException(status_code=403, detail="Доступно только администраторам")
    return user_id


@app.on_event("startup")
def startup():
    # В CI (pytest без MySQL) установите CI_SKIP_STARTUP=1
    if os.getenv("CI_SKIP_STARTUP", "").lower() in ("1", "true", "yes"):
        return
    _ensure_image_url_column()
    _ensure_document_columns()
    _ensure_roles_and_modes_tables()
    _ensure_llm_settings_table()
    db = SessionLocal()
    try:
        _seed_response_modes_if_empty(db)
        _sync_app_roles_from_env(db)
        _seed_llm_settings_if_empty(db)
    finally:
        db.close()
    # Авто-инициализация БД и тестовых данных только если явно включена в .env
    if not INIT_DB_ON_STARTUP:
        return

    # Создаём все таблицы (для тестов: tariff, users, android_dialogs, user_messages, bot_messages)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Тестовый тариф (нужен для FK у пользователя)
        tariff = db.query(models.BrenksEssenceTariff).filter(
            models.BrenksEssenceTariff.id_tariff == 1
        ).first()
        if not tariff:
            db.add(models.BrenksEssenceTariff(
                id_tariff=1,
                name_tariff="Тестовый",
                price_tariff="0",
                img_tariff=None,
            ))
            db.commit()

        # Тестовый пользователь: логин test, пароль 1234 (activation=1, ban=1 — можно заходить)
        user = db.query(models.BrenksEssenceUser).filter(
            models.BrenksEssenceUser.id_user == 1
        ).first()
        if not user:
            db.add(models.BrenksEssenceUser(
                id_user=1,
                username="test",
                password=_password_hash("1234"),
                activation=1,
                ban=1,
                id_tariff=1,
            ))
            db.commit()
    finally:
        db.close()


@app.post("/auth/login", response_model=schemas.LoginResponse)
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(models.BrenksEssenceUser)
        .filter(models.BrenksEssenceUser.username == body.username)
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    expected = _password_hash(body.password)
    if (user.password or "") != expected:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if user.activation == 0:
        raise HTTPException(
            status_code=403,
            detail="Активируйте аккаунт в личном кабинете на сайте",
        )
    # В вашей схеме ban=0 означает «аккаунт заблокирован»
    if user.ban == 0:
        raise HTTPException(
            status_code=403,
            detail="Аккаунт заблокирован",
        )
    token = _create_token(user.id_user)
    roles = _user_roles_list(db, user.id_user)
    return schemas.LoginResponse(
        id_user=user.id_user,
        username=user.username or body.username,
        token=token,
        roles=roles,
    )


@app.get("/auth/me", response_model=schemas.MeResponse)
def auth_me(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    user = (
        db.query(models.BrenksEssenceUser)
        .filter(models.BrenksEssenceUser.id_user == current_user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return schemas.MeResponse(
        id_user=user.id_user,
        username=user.username or "",
        roles=_user_roles_list(db, current_user_id),
    )


@app.get("/response-modes", response_model=List[schemas.ResponseMode])
def list_response_modes_public(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    rows = (
        db.query(models.AndroidResponseMode)
        .filter(models.AndroidResponseMode.is_active == True)  # noqa: E712
        .order_by(models.AndroidResponseMode.sort_order.asc(), models.AndroidResponseMode.id_mode.asc())
        .all()
    )
    return [_mode_to_schema(m) for m in rows]


@app.get("/response-modes/manage", response_model=List[schemas.ResponseMode])
def list_response_modes_manage(
    db: Session = Depends(get_db),
    _: int = Depends(require_prompt_engineer),
):
    rows = (
        db.query(models.AndroidResponseMode)
        .order_by(models.AndroidResponseMode.sort_order.asc(), models.AndroidResponseMode.id_mode.asc())
        .all()
    )
    return [_mode_to_schema(m) for m in rows]


@app.post("/response-modes", response_model=schemas.ResponseMode)
def create_response_mode(
    body: schemas.ResponseModeCreate,
    db: Session = Depends(get_db),
    _: int = Depends(require_prompt_engineer),
):
    key = (body.template_key or "").strip().lower()
    if not re.match(r"^[a-z][a-z0-9_]{1,62}$", key):
        raise HTTPException(
            status_code=400,
            detail="Ключ режима: латиница, цифры и _, начинается с буквы (2–63 символа)",
        )
    exists = (
        db.query(models.AndroidResponseMode)
        .filter(models.AndroidResponseMode.template_key == key)
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="Такой ключ режима уже есть")
    m = models.AndroidResponseMode(
        template_key=key,
        title=(body.title or "").strip()[:255] or key,
        system_prompt=body.system_prompt or "",
        sort_order=int(body.sort_order),
        is_active=bool(body.is_active),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _mode_to_schema(m)


@app.patch("/response-modes/{mode_id}", response_model=schemas.ResponseMode)
def update_response_mode(
    mode_id: int,
    body: schemas.ResponseModeUpdate,
    db: Session = Depends(get_db),
    _: int = Depends(require_prompt_engineer),
):
    m = (
        db.query(models.AndroidResponseMode)
        .filter(models.AndroidResponseMode.id_mode == mode_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Режим не найден")
    if body.title is not None:
        m.title = body.title.strip()[:255]
    if body.system_prompt is not None:
        m.system_prompt = body.system_prompt
    if body.sort_order is not None:
        m.sort_order = int(body.sort_order)
    if body.is_active is not None:
        m.is_active = bool(body.is_active)
    db.commit()
    db.refresh(m)
    return _mode_to_schema(m)


@app.delete("/response-modes/{mode_id}", status_code=204)
def delete_response_mode(
    mode_id: int,
    db: Session = Depends(get_db),
    _: int = Depends(require_prompt_engineer),
):
    m = (
        db.query(models.AndroidResponseMode)
        .filter(models.AndroidResponseMode.id_mode == mode_id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Режим не найден")
    # нельзя удалить базовый default
    if m.template_key == "default":
        raise HTTPException(status_code=400, detail="Режим «default» нельзя удалить")
    db.delete(m)
    db.commit()
    return None


@app.get("/admin/server-status", response_model=schemas.AdminServerStatus)
def admin_server_status(
    db: Session = Depends(get_db),
    _: int = Depends(require_admin),
):
    ok = False
    err: Optional[str] = None
    llm_row = _get_llm_settings_row(db)
    ping_json: dict = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": float(llm_row.temperature),
    }
    if llm_row.top_p is not None:
        ping_json["top_p"] = float(llm_row.top_p)
    try:
        headers = {"Content-Type": "application/json"}
        if MODEL_API_KEY:
            headers["Authorization"] = f"Bearer {MODEL_API_KEY}"
        r = requests.post(
            MODEL_ENDPOINT,
            json=ping_json,
            headers=headers,
            timeout=8,
        )
        ok = r.status_code < 500
        if not ok:
            err = f"HTTP {r.status_code}"
    except Exception as e:
        err = str(e)[:200]
    return schemas.AdminServerStatus(
        api_ok=True,
        llm_endpoint=MODEL_ENDPOINT,
        llm_model=MODEL_NAME,
        llm_reachable=ok,
        llm_error=err,
    )


@app.get("/admin/stats", response_model=schemas.AdminStats)
def admin_stats(
    db: Session = Depends(get_db),
    _: int = Depends(require_admin),
):
    return schemas.AdminStats(
        users_count=db.query(models.BrenksEssenceUser).count(),
        chats_count=db.query(models.AndroidDialog).count(),
        user_messages_count=db.query(models.AndroidUserMessage).count(),
        bot_messages_count=db.query(models.AndroidBotMessage).count(),
        response_modes_count=db.query(models.AndroidResponseMode).count(),
    )


@app.get("/admin/llm-settings", response_model=schemas.LlmSettings)
def get_llm_settings(
    db: Session = Depends(get_db),
    _: int = Depends(require_admin),
):
    row = _get_llm_settings_row(db)
    return _llm_row_to_schema(row)


def _validate_llm_settings_values(body: schemas.LlmSettings) -> None:
    t = float(body.temperature)
    if not 0.0 <= t <= 2.0:
        raise HTTPException(status_code=400, detail="temperature: допустимо 0..2")
    m = int(body.max_tokens)
    if m < 1 or m > 32768:
        raise HTTPException(status_code=400, detail="max_tokens: допустимо 1..32768")
    if body.top_p is not None:
        v = float(body.top_p)
        if not 0.0 <= v <= 1.0:
            raise HTTPException(status_code=400, detail="top_p: допустимо 0..1")
    if body.frequency_penalty is not None:
        v = float(body.frequency_penalty)
        if not -2.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="frequency_penalty: допустимо -2..2")
    if body.presence_penalty is not None:
        v = float(body.presence_penalty)
        if not -2.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="presence_penalty: допустимо -2..2")
    if body.repeat_penalty is not None:
        v = float(body.repeat_penalty)
        if not 0.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="repeat_penalty: допустимо 0..2 (KoboldCpp)")


@app.put("/admin/llm-settings", response_model=schemas.LlmSettings)
def put_llm_settings(
    body: schemas.LlmSettings,
    db: Session = Depends(get_db),
    _: int = Depends(require_admin),
):
    """Полная замена параметров генерации (опциональные поля null = не передавать в запрос к LLM)."""
    _validate_llm_settings_values(body)
    _seed_llm_settings_if_empty(db)
    row = db.get(models.AndroidLlmSettings, 1)
    if row is None:
        raise HTTPException(status_code=500, detail="Не удалось загрузить настройки LLM")
    row.temperature = float(body.temperature)
    row.max_tokens = int(body.max_tokens)
    row.top_p = body.top_p
    row.frequency_penalty = body.frequency_penalty
    row.presence_penalty = body.presence_penalty
    row.repeat_penalty = body.repeat_penalty
    db.commit()
    db.refresh(row)
    return _llm_row_to_schema(row)


@app.patch("/admin/llm-settings", response_model=schemas.LlmSettings)
def patch_llm_settings(
    body: schemas.LlmSettingsUpdate,
    db: Session = Depends(get_db),
    _: int = Depends(require_admin),
):
    _seed_llm_settings_if_empty(db)
    row = db.get(models.AndroidLlmSettings, 1)
    if row is None:
        raise HTTPException(status_code=500, detail="Не удалось загрузить настройки LLM")

    if body.temperature is not None:
        t = float(body.temperature)
        if not 0.0 <= t <= 2.0:
            raise HTTPException(status_code=400, detail="temperature: допустимо 0..2")
        row.temperature = t
    if body.max_tokens is not None:
        m = int(body.max_tokens)
        if m < 1 or m > 32768:
            raise HTTPException(status_code=400, detail="max_tokens: допустимо 1..32768")
        row.max_tokens = m
    if body.top_p is not None:
        v = float(body.top_p)
        if not 0.0 <= v <= 1.0:
            raise HTTPException(status_code=400, detail="top_p: допустимо 0..1")
        row.top_p = v
    if body.frequency_penalty is not None:
        v = float(body.frequency_penalty)
        if not -2.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="frequency_penalty: допустимо -2..2")
        row.frequency_penalty = v
    if body.presence_penalty is not None:
        v = float(body.presence_penalty)
        if not -2.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="presence_penalty: допустимо -2..2")
        row.presence_penalty = v
    if body.repeat_penalty is not None:
        v = float(body.repeat_penalty)
        if not 0.0 <= v <= 2.0:
            raise HTTPException(status_code=400, detail="repeat_penalty: допустимо 0..2 (KoboldCpp)")
        row.repeat_penalty = v

    tmp = _llm_row_to_schema(row)
    _validate_llm_settings_values(tmp)

    db.commit()
    db.refresh(row)
    return _llm_row_to_schema(row)


@app.get("/users/{user_id}/tariff", response_model=schemas.TariffResponse)
def get_user_tariff(
    user_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому пользователю")
    """Тариф пользователя по id_user (из brenks_essence_users.id_tariff → brenks_essence_tariff)."""
    user = (
        db.query(models.BrenksEssenceUser)
        .filter(models.BrenksEssenceUser.id_user == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id_tariff is None:
        raise HTTPException(status_code=404, detail="Tariff not set")
    tariff = (
        db.query(models.BrenksEssenceTariff)
        .filter(models.BrenksEssenceTariff.id_tariff == user.id_tariff)
        .first()
    )
    if not tariff:
        raise HTTPException(status_code=404, detail="Tariff not found")
    return tariff


def _dialog_is_hidden(dialog_id: int, db: Session) -> bool:
    """Диалог считаем скрытым, если все user_messages в нём имеют is_hidden=True."""
    user_msgs = (
        db.query(models.AndroidUserMessage)
        .filter(models.AndroidUserMessage.id_android_dialogs == dialog_id)
        .all()
    )
    if not user_msgs:
        return False
    return all(m.is_hidden for m in user_msgs)


def _get_dialog_history(dialog_id: int, db: Session) -> List[Any]:
    """Собирает историю сообщений диалога (user + bot) по дате для контекста LLM."""
    user_msgs = (
        db.query(models.AndroidUserMessage)
        .filter(models.AndroidUserMessage.id_android_dialogs == dialog_id)
        .order_by(models.AndroidUserMessage.date_user_android_message.asc())
        .all()
    )
    bot_msgs = (
        db.query(models.AndroidBotMessage)
        .filter(models.AndroidBotMessage.id_android_dialogs == dialog_id)
        .order_by(models.AndroidBotMessage.date_bot_android_message.asc())
        .all()
    )
    # Объединяем и сортируем по дате (у сообщений есть .date_*)
    class _Msg:
        __slots__ = (
            "sender", "text", "dt", "image_url",
            "document_text", "document_name", "document_url", "document_mime",
        )
        def __init__(
            self,
            sender: str,
            text: str,
            dt,
            image_url: Optional[str] = None,
            document_text: Optional[str] = None,
            document_name: Optional[str] = None,
            document_url: Optional[str] = None,
            document_mime: Optional[str] = None,
        ):
            self.sender = sender
            self.text = text
            self.dt = dt
            self.image_url = image_url
            self.document_text = document_text
            self.document_name = document_name
            self.document_url = document_url
            self.document_mime = document_mime
    out = []
    for m in user_msgs:
        out.append(_Msg(
            "user",
            m.user_andoid_message,
            m.date_user_android_message,
            getattr(m, "image_url", None),
            getattr(m, "document_text", None),
            getattr(m, "document_name", None),
            getattr(m, "document_url", None),
            getattr(m, "document_mime", None),
        ))
    for m in bot_msgs:
        out.append(_Msg("bot", m.bot_android_message, m.date_bot_android_message))
    out.sort(key=lambda x: x.dt or "")
    return out


def _mime_from_upload_path(p: Path) -> str:
    ext = p.suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "png":
        return "image/png"
    if ext == "webp":
        return "image/webp"
    if ext == "gif":
        return "image/gif"
    return "application/octet-stream"


def _uploads_path_from_url(url: str) -> Optional[Path]:
    """Путь к файлу в UPLOADS_DIR по URL вида /uploads/name.ext."""
    if not url or not url.startswith("/uploads/"):
        return None
    rel = url[len("/uploads/"):]
    p = (UPLOADS_DIR / rel).resolve()
    if UPLOADS_DIR not in p.parents and p != UPLOADS_DIR:
        return None
    if not p.exists() or not p.is_file():
        return None
    return p


def _image_url_to_data_url(image_url: str) -> Optional[str]:
    """
    Превращает /uploads/xxx.ext в data:<mime>;base64,... чтобы передать картинку модели.
    Возвращает None, если файл не найден/слишком большой.
    """
    p = _uploads_path_from_url(image_url)
    if p is None:
        return None
    raw = p.read_bytes()
    # ограничим размер для LLM (чтобы не улететь в огромный payload)
    if len(raw) > 2 * 1024 * 1024:
        return None
    mime = _mime_from_upload_path(p)
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pdf_to_page_data_urls(pdf_path: Path) -> List[str]:
    """
    Рендерит страницы PDF в JPEG (data URL) для vision-моделей (Qwen-VL и т.д.).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []

    max_pages = max(1, int(os.getenv("MAX_PDF_PAGES", "5")))
    long_edge = max(512, int(os.getenv("MAX_PDF_PAGE_LONG_EDGE", "1400")))
    max_jpeg_bytes = int(os.getenv("MAX_PDF_PAGE_JPEG_BYTES", "1800000"))

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    try:
        n = min(max_pages, len(doc))
        out: List[str] = []
        for i in range(n):
            page = doc[i]
            rect = page.rect
            w, h = float(rect.width), float(rect.height)
            if w <= 0 or h <= 0:
                continue
            scale = min(long_edge / max(w, h), 3.0)
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_bytes: bytes
            try:
                img_bytes = pix.tobytes("jpeg")
            except Exception:
                img_bytes = pix.tobytes("png")
            # при перегрузе уменьшаем
            tries = 0
            while len(img_bytes) > max_jpeg_bytes and tries < 4:
                scale *= 0.65
                mat = fitz.Matrix(scale, scale)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                try:
                    img_bytes = pix.tobytes("jpeg")
                except Exception:
                    img_bytes = pix.tobytes("png")
                tries += 1
            b64 = base64.b64encode(img_bytes).decode("ascii")
            mime = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
            out.append(f"data:{mime};base64,{b64}")
        return out
    finally:
        doc.close()


def _pdf_extract_text_fallback(pdf_path: Path, max_chars: int = 8000) -> str:
    """Плоский текст из PDF (если vision недоступен или как дополнение)."""
    try:
        import fitz
    except ImportError:
        return ""
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return ""
    try:
        parts = []
        for i in range(min(len(doc), int(os.getenv("MAX_PDF_PAGES", "20")))):
            parts.append(doc[i].get_text("text") or "")
        txt = "\n".join(parts).strip()
        return txt[:max_chars]
    except Exception:
        return ""
    finally:
        doc.close()


def _docx_extract_text(raw: bytes) -> str:
    """
    Минимальный извлекатель текста из docx без внешних зависимостей.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        # вытаскиваем текстовые узлы
        parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.DOTALL)
        txt = "\n".join([re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()])
        return txt.strip()
    except Exception:
        return ""


def _image_url_to_public_url(image_url: str) -> Optional[str]:
    """
    Превращает /uploads/xxx.ext в абсолютный URL, если задан PUBLIC_API_BASE_URL.
    """
    if not image_url or not image_url.startswith("/uploads/"):
        return None
    if not PUBLIC_API_BASE_URL:
        return None
    base = _ensure_url_scheme(PUBLIC_API_BASE_URL).rstrip("/")
    return f"{base}{image_url}"


@app.get("/users/{user_id}/chats", response_model=List[schemas.Chat])
def list_chats(
    user_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому пользователю")
    dialogs = (
        db.query(models.AndroidDialog)
        .filter(models.AndroidDialog.id_users == user_id)
        .order_by(models.AndroidDialog.date_created.desc())
        .all()
    )
    result = []
    for d in dialogs:
        if _dialog_is_hidden(d.id_android_dialogs, db):
            continue
        last_user = (
            db.query(models.AndroidUserMessage)
            .filter(models.AndroidUserMessage.id_android_dialogs == d.id_android_dialogs)
            .order_by(models.AndroidUserMessage.date_user_android_message.desc())
            .first()
        )
        last_bot = (
            db.query(models.AndroidBotMessage)
            .filter(models.AndroidBotMessage.id_android_dialogs == d.id_android_dialogs)
            .order_by(models.AndroidBotMessage.date_bot_android_message.desc())
            .first()
        )
        last_text = None
        lu = last_user.date_user_android_message if last_user else None
        lb = last_bot.date_bot_android_message if last_bot else None
        if lb and (not lu or (lu and lb >= lu)):
            last_text = last_bot.bot_android_message
        elif last_user:
            last_text = last_user.user_andoid_message
        elif last_bot:
            last_text = last_bot.bot_android_message
        result.append(
            schemas.Chat(
                id=d.id_android_dialogs,
                title=d.name_dialog,
                created_at=d.date_created,
                last_message=last_text,
                is_hidden=False,
            )
        )
    return result


@app.post("/users/{user_id}/chats", response_model=schemas.Chat)
def create_chat(
    user_id: int,
    chat: schemas.ChatCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    if current_user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому пользователю")
    new_dialog = models.AndroidDialog(name_dialog=chat.title, id_users=user_id)
    db.add(new_dialog)
    db.commit()
    db.refresh(new_dialog)
    return schemas.Chat(
        id=new_dialog.id_android_dialogs,
        title=new_dialog.name_dialog,
        created_at=new_dialog.date_created,
        last_message=None,
        is_hidden=False,
    )


@app.get("/chats/{chat_id}/messages", response_model=List[schemas.Message])
def get_messages(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    dialog = db.query(models.AndroidDialog).filter(models.AndroidDialog.id_android_dialogs == chat_id).first()
    if not dialog:
        raise HTTPException(status_code=404, detail="Chat not found")
    if dialog.id_users != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")

    user_msgs = (
        db.query(models.AndroidUserMessage)
        .filter(models.AndroidUserMessage.id_android_dialogs == chat_id)
        .order_by(models.AndroidUserMessage.date_user_android_message.asc())
        .all()
    )
    bot_msgs = (
        db.query(models.AndroidBotMessage)
        .filter(models.AndroidBotMessage.id_android_dialogs == chat_id)
        .order_by(models.AndroidBotMessage.date_bot_android_message.asc())
        .all()
    )
    class _Row:
        def __init__(
            self,
            id_: int,
            sender: str,
            text: str,
            created_at,
            image_url: Optional[str] = None,
            document_url: Optional[str] = None,
            document_name: Optional[str] = None,
            document_mime: Optional[str] = None,
        ):
            self.id = id_
            self.sender = sender
            self.text = text
            self.created_at = created_at
            self.image_url = image_url
            self.document_url = document_url
            self.document_name = document_name
            self.document_mime = document_mime
    merged = []
    for m in user_msgs:
        merged.append((
            m.date_user_android_message,
            _Row(
                m.id_user_android_message,
                "user",
                m.user_andoid_message,
                m.date_user_android_message,
                getattr(m, "image_url", None),
                getattr(m, "document_url", None),
                getattr(m, "document_name", None),
                getattr(m, "document_mime", None),
            )
        ))
    for m in bot_msgs:
        merged.append((
            m.date_bot_android_message,
            _Row(m.id_bot_android_message, "bot", m.bot_android_message, m.date_bot_android_message, None)
        ))
    merged.sort(key=lambda x: x[0] or "")
    return [
        schemas.Message(
            id=r.id,
            text=r.text,
            sender=r.sender,
            created_at=r.created_at,
            image_url=r.image_url,
            document_url=r.document_url,
            document_name=r.document_name,
            document_mime=r.document_mime,
        )
        for _, r in merged
    ]


@app.post("/chats/{chat_id}/messages", response_model=List[schemas.Message])
def post_message(
    chat_id: int,
    message: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    dialog = db.query(models.AndroidDialog).filter(models.AndroidDialog.id_android_dialogs == chat_id).first()
    if not dialog:
        raise HTTPException(status_code=404, detail="Chat not found")
    if dialog.id_users != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")

    if len(message.text or "") > MAX_MESSAGE_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Сообщение слишком длинное. Максимум {MAX_MESSAGE_CHARS} символов.",
        )

    image_url = None
    document_url = None
    document_name = None
    document_mime = None
    document_text = None
    if message.image_base64:
        try:
            raw = base64.b64decode(message.image_base64, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Некорректная картинка (base64)")

        if len(raw) > 6 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Картинка слишком большая (макс 6MB)")

        ext = None
        kind = imghdr.what(None, h=raw)
        if kind in ("jpeg", "png", "gif", "webp"):
            ext = "jpg" if kind == "jpeg" else kind
        else:
            # fallback по mime, если imghdr не распознал
            if (message.image_mime or "").lower() == "image/jpeg":
                ext = "jpg"
            elif (message.image_mime or "").lower() == "image/png":
                ext = "png"
            elif (message.image_mime or "").lower() == "image/webp":
                ext = "webp"
            elif (message.image_mime or "").lower() == "image/gif":
                ext = "gif"

        if not ext:
            raise HTTPException(status_code=400, detail="Неподдерживаемый формат картинки")

        fname = f"chat_{chat_id}_{int(time.time())}_{hashlib.sha256(raw).hexdigest()[:12]}.{ext}"
        out_path = UPLOADS_DIR / fname
        out_path.write_bytes(raw)
        image_url = f"/uploads/{fname}"

    if message.document_base64:
        try:
            raw_doc = base64.b64decode(message.document_base64, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Некорректный документ (base64)")

        if len(raw_doc) > 12 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Документ слишком большой (макс 12MB)")

        document_mime = (message.document_mime or "").lower()
        document_name = (message.document_name or "document").strip()[:255]
        # ограничим типы
        if document_mime not in (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ):
            raise HTTPException(status_code=400, detail="Неподдерживаемый тип документа")

        ext = "pdf" if document_mime == "application/pdf" else "docx"
        fname = f"chat_{chat_id}_{int(time.time())}_{hashlib.sha256(raw_doc).hexdigest()[:12]}.{ext}"
        out_path = UPLOADS_DIR / fname
        out_path.write_bytes(raw_doc)
        document_url = f"/uploads/{fname}"

        # извлечение текста: docx — без зависимостей; pdf — PyMuPDF (доп. к vision)
        if ext == "docx":
            document_text = _docx_extract_text(raw_doc)
        elif ext == "pdf":
            document_text = _pdf_extract_text_fallback(UPLOADS_DIR / fname)

    user_msg = models.AndroidUserMessage(
        id_android_dialogs=chat_id,
        id_users=dialog.id_users,
        user_andoid_message=message.text or "",
    )
    if image_url:
        user_msg.image_url = image_url
    if document_url:
        user_msg.document_url = document_url
        user_msg.document_name = document_name
        user_msg.document_mime = document_mime
        user_msg.document_text = document_text
    db.add(user_msg)
    db.flush()

    effective_template = _validate_active_template(db, message.template)
    history = _get_dialog_history(chat_id, db)
    bot_text = generate_bot_reply(db, history, template=effective_template)
    bot_msg = models.AndroidBotMessage(
        id_android_dialogs=chat_id,
        id_users=dialog.id_users,
        bot_android_message=bot_text,
        tokens_android=None,
    )
    db.add(bot_msg)
    db.commit()
    db.refresh(user_msg)
    db.refresh(bot_msg)

    return [
        schemas.Message(
            id=user_msg.id_user_android_message,
            text=user_msg.user_andoid_message,
            sender="user",
            created_at=user_msg.date_user_android_message,
            image_url=image_url,
            document_url=document_url,
            document_name=document_name,
            document_mime=document_mime,
        ),
        schemas.Message(id=bot_msg.id_bot_android_message, text=bot_msg.bot_android_message, sender="bot", created_at=bot_msg.date_bot_android_message),
    ]


@app.patch("/chats/{chat_id}", response_model=schemas.Chat)
def rename_chat(
    chat_id: int,
    payload: schemas.ChatUpdate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    dialog = db.query(models.AndroidDialog).filter(models.AndroidDialog.id_android_dialogs == chat_id).first()
    if not dialog:
        raise HTTPException(status_code=404, detail="Chat not found")
    if dialog.id_users != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")

    dialog.name_dialog = payload.title
    db.commit()
    db.refresh(dialog)

    last_user = (
        db.query(models.AndroidUserMessage)
        .filter(models.AndroidUserMessage.id_android_dialogs == dialog.id_android_dialogs)
        .order_by(models.AndroidUserMessage.date_user_android_message.desc())
        .first()
    )
    last_bot = (
        db.query(models.AndroidBotMessage)
        .filter(models.AndroidBotMessage.id_android_dialogs == dialog.id_android_dialogs)
        .order_by(models.AndroidBotMessage.date_bot_android_message.desc())
        .first()
    )
    last_text = None
    lu = last_user.date_user_android_message if last_user else None
    lb = last_bot.date_bot_android_message if last_bot else None
    if lb and (not lu or (lu and lb >= lu)):
        last_text = last_bot.bot_android_message
    elif last_user:
        last_text = last_user.user_andoid_message
    elif last_bot:
        last_text = last_bot.bot_android_message

    return schemas.Chat(
        id=dialog.id_android_dialogs,
        title=dialog.name_dialog,
        created_at=dialog.date_created,
        last_message=last_text,
        is_hidden=False,
    )


@app.delete("/chats/{chat_id}", status_code=204)
def hide_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    dialog = db.query(models.AndroidDialog).filter(models.AndroidDialog.id_android_dialogs == chat_id).first()
    if not dialog:
        raise HTTPException(status_code=404, detail="Chat not found")
    if dialog.id_users != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")

    db.query(models.AndroidUserMessage).filter(
        models.AndroidUserMessage.id_android_dialogs == chat_id
    ).update({models.AndroidUserMessage.is_hidden: True})
    db.commit()
    return None


def _resolve_system_prompt(db: Session, template: Optional[str]) -> str:
    """Системный промпт из БД; ключ по умолчанию — default."""
    key = (template or "").strip() or "default"
    row = (
        db.query(models.AndroidResponseMode)
        .filter(
            models.AndroidResponseMode.template_key == key,
            models.AndroidResponseMode.is_active == True,  # noqa: E712
        )
        .first()
    )
    if row:
        return row.system_prompt
    return _legacy_builtin_prompt(template)


def _legacy_builtin_prompt(template: Optional[str]) -> str:
    """
    Запасные встроенные промпты, если в БД нет строки (миграция / сбой).
    """
    if template == "devils_advocate":
        # Режим «Адвокат дьявола» (критическое мышление)
        return (
            "Ты — «адвокат дьявола», циничный критик и опытный дебатёр. "
            "Твоя задача — найти слабые места в утверждении пользователя. "
            "Не соглашайся с пользователем. Приводи контраргументы. "
            "Будь вежлив, но твёрд. Найди как минимум 3 причины, "
            "почему идея пользователя может провалиться."
        )

    if template == "analogy":
        # Режим «Генератор аналогий»
        return (
            "Ты — генератор аналогий для обучения. Объясняй термины и идеи пользователя "
            "исключительно через аналогии из реальной жизни (еда, машины, животные, быт и т.п.). "
            "Не используй сухие академические определения. "
            "Всегда начинай ответ с фразы: «Представь, что это ...»."
        )

    if template == "commit_message":
        # Режим «Commit Message Creator»
        return (
            "Ты — Senior Developer. Тебе дают diff или описание изменений в коде. "
            "Проанализируй изменения и напиши одно короткое, но ёмкое сообщение для git commit "
            "в формате: [Тип]: Описание. Используй только типы: feat, fix, refactor, docs. "
            "Не пиши ничего, кроме одного commit message."
        )

    if template == "smm_clickbait":
        # Режим «SMM-менеджер / Кликбейт»
        return (
            "Ты — SMM-менеджер и креативный копирайтер. Перепиши текст пользователя "
            "для публикации в соцсетях (Twitter/Telegram). Сделай его захватывающим, "
            "добавь эмодзи, хэштеги и призыв к действию. "
            "Сгенерируй ровно 3 варианта: 1) Сдержанный, 2) Весёлый, 3) Агрессивный. "
            "Ясно пометь каждый вариант заголовком и не добавляй ничего лишнего."
        )

    # Режим по умолчанию
    return "Ты — Элион, дружелюбный помощник."


def generate_bot_reply(db: Session, history: List[Any], template: Optional[str] = None) -> str:
    """history: список объектов с .sender и .text (user/bot сообщения по порядку)."""
    system_prompt = _resolve_system_prompt(db, template)
    system_prompt += " Отвечай кратко и по делу (обычно 2–5 предложений)."
    
    if len(history) > MAX_CONTEXT_MESSAGES:
        history = history[-MAX_CONTEXT_MESSAGES:]

    conversation = [
        {"role": "system", "content": system_prompt},
    ]
    for msg in history:
        role = "assistant" if getattr(msg, "sender", "") != "user" else "user"
        text_content = getattr(msg, "text", "") or ""
        image_url = getattr(msg, "image_url", None)
        doc_text = getattr(msg, "document_text", None)
        doc_name = getattr(msg, "document_name", None)
        doc_url = getattr(msg, "document_url", None)
        doc_mime = (getattr(msg, "document_mime", None) or "").lower()
        is_pdf = (
            role == "user"
            and doc_url
            and (doc_mime == "application/pdf" or str(doc_url).lower().endswith(".pdf"))
        )
        if role == "user" and image_url:
            # Для KoboldCpp (/v1/chat/completions) надёжнее всего работает data: URL.
            # Обычный URL оставляем как fallback, если data: не получилось собрать.
            url = _image_url_to_data_url(image_url)
            if not url:
                url = _image_url_to_public_url(image_url)
            if url:
                conversation.append({
                    "role": role,
                    "content": [
                        {"type": "text", "text": text_content},
                        {"type": "image_url", "image_url": {"url": url}},
                    ]
                })
                continue
        # PDF: страницы как картинки для vision (Qwen-VL / KoboldCpp)
        if is_pdf:
            pdf_path = _uploads_path_from_url(doc_url)
            page_urls: List[str] = []
            if pdf_path is not None:
                page_urls = _pdf_to_page_data_urls(pdf_path)
            if page_urls:
                label = f"[PDF: {doc_name}]" if doc_name else "[PDF]"
                text_block = (text_content + "\n\n" + label).strip() if text_content else label
                content: List[Any] = [{"type": "text", "text": text_block}]
                for u in page_urls:
                    content.append({"type": "image_url", "image_url": {"url": u}})
                conversation.append({"role": role, "content": content})
                continue
        if role == "user" and doc_text:
            prefix = f"\n\n[Документ: {doc_name}]\n" if doc_name else "\n\n[Документ]\n"
            # ограничим, чтобы не раздувать контекст
            trimmed = doc_text[:8000]
            conversation.append({"role": role, "content": (text_content + prefix + trimmed).strip()})
            continue
        conversation.append({"role": role, "content": text_content})

    llm_row = _get_llm_settings_row(db)
    payload: dict = {
        "model": MODEL_NAME,
        "messages": conversation,
    }
    _apply_llm_row_to_payload(llm_row, payload)
    headers = {"Content-Type": "application/json"}
    if MODEL_API_KEY:
        headers["Authorization"] = f"Bearer {MODEL_API_KEY}"

    try:
        with LLM_LOCK:
            response = requests.post(
                MODEL_ENDPOINT, json=payload, headers=headers, timeout=120
            )
        response.raise_for_status()
        data = response.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "Нет ответа от модели.")
        )
    except requests.exceptions.Timeout:
        return "Элион недоступен: превышено время ожидания ответа."
    except Exception:
        return "Элион недоступен: временная ошибка сервиса."