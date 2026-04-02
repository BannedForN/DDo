from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, Float, func
from sqlalchemy.orm import relationship

from database import Base


class BrenksEssenceTariff(Base):
    """Таблица из внешней БД: brenks_essence_tariff. Не создаётся приложением."""
    __tablename__ = "brenks_essence_tariff"

    id_tariff = Column(Integer, primary_key=True, index=True)
    name_tariff = Column(String(50), nullable=True)
    price_tariff = Column(String(11), nullable=True)
    img_tariff = Column(String(255), nullable=True)


class BrenksEssenceUser(Base):
    """Таблица из внешней БД: brenks_essence_users. Не создаётся приложением."""
    __tablename__ = "brenks_essence_users"

    id_user = Column(Integer, primary_key=True, index=True)
    user_company = Column(Integer, nullable=True)
    date_act = Column(DateTime, nullable=True)
    user_ip = Column(String(50), nullable=True)
    activation = Column(Integer, nullable=False, default=0)
    ban = Column(Integer, nullable=False, default=0)
    id_tariff = Column(
        Integer,
        ForeignKey("brenks_essence_tariff.id_tariff"),
        nullable=True,
    )
    username = Column(String(50), nullable=True, index=True)
    password = Column(String(50), nullable=True)

    tariff = relationship(
        "BrenksEssenceTariff",
        foreign_keys=[id_tariff],
    )
    dialogs = relationship(
        "AndroidDialog",
        back_populates="user",
        foreign_keys="AndroidDialog.id_users",
        cascade="all, delete-orphan",
    )


class AndroidDialog(Base):
    __tablename__ = "brenks_essence_android_dialogs"

    id_android_dialogs = Column(Integer, primary_key=True, index=True)
    id_users = Column(
        Integer,
        ForeignKey("brenks_essence_users.id_user"),
        nullable=False,
    )
    name_dialog = Column(String(255), nullable=False)
    date_created = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship(
        "BrenksEssenceUser",
        back_populates="dialogs",
        foreign_keys=[id_users],
    )
    user_messages = relationship(
        "AndroidUserMessage",
        back_populates="dialog",
        cascade="all, delete-orphan",
    )
    bot_messages = relationship(
        "AndroidBotMessage",
        back_populates="dialog",
        cascade="all, delete-orphan",
    )


class AndroidUserMessage(Base):
    __tablename__ = "brenks_essence_android_user_messages"

    id_user_android_message = Column(Integer, primary_key=True, index=True)
    id_android_dialogs = Column(
        Integer,
        ForeignKey("brenks_essence_android_dialogs.id_android_dialogs"),
        nullable=False,
    )
    id_users = Column(
        Integer,
        ForeignKey("brenks_essence_users.id_user"),
        nullable=False,
    )
    user_andoid_message = Column(Text, nullable=False)
    # URL/путь к загруженному изображению (например: /uploads/xxx.jpg)
    image_url = Column(String(512), nullable=True)
    document_url = Column(String(512), nullable=True)
    document_name = Column(String(255), nullable=True)
    document_mime = Column(String(128), nullable=True)
    # Извлечённый текст (для передачи в LLM); может быть большим
    document_text = Column(Text, nullable=True)
    date_user_android_message = Column(DateTime(timezone=True), server_default=func.now())
    is_hidden = Column(Boolean, nullable=False, server_default="0")

    dialog = relationship("AndroidDialog", back_populates="user_messages")


class AndroidUserAppRole(Base):
    """Роли приложения: prompt_engineer, admin (не путать с полями внешней БД)."""
    __tablename__ = "brenks_essence_android_user_roles"

    id_user = Column(Integer, primary_key=True, nullable=False)
    role = Column(String(32), primary_key=True, nullable=False)


class AndroidResponseMode(Base):
    """Режимы ответа (системный промпт), настраивает промпт-инженер."""
    __tablename__ = "brenks_essence_android_response_modes"

    id_mode = Column(Integer, primary_key=True, autoincrement=True, index=True)
    template_key = Column(String(64), unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False)
    system_prompt = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    date_created = Column(DateTime(timezone=True), server_default=func.now())


class AndroidLlmSettings(Base):
    """Параметры генерации для LLM (одна строка id=1), правит администратор."""
    __tablename__ = "brenks_essence_android_llm_settings"

    id_settings = Column(Integer, primary_key=True, default=1)
    temperature = Column(Float, nullable=False, default=0.7)
    max_tokens = Column(Integer, nullable=False, default=512)
    top_p = Column(Float, nullable=True)
    frequency_penalty = Column(Float, nullable=True)
    presence_penalty = Column(Float, nullable=True)
    repeat_penalty = Column(Float, nullable=True)


class AndroidBotMessage(Base):
    __tablename__ = "brenks_essence_android_bot_messages"

    id_bot_android_message = Column(Integer, primary_key=True, index=True)
    id_android_dialogs = Column(
        Integer,
        ForeignKey("brenks_essence_android_dialogs.id_android_dialogs"),
        nullable=False,
    )
    id_users = Column(
        Integer,
        ForeignKey("brenks_essence_users.id_user"),
        nullable=False,
    )
    bot_android_message = Column(Text, nullable=False)
    date_bot_android_message = Column(DateTime(timezone=True), server_default=func.now())
    tokens_android = Column(Integer, nullable=True, default=0)

    dialog = relationship("AndroidDialog", back_populates="bot_messages")
