from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    id_user: int
    username: str
    token: str
    message: str = "OK"
    roles: List[str] = Field(default_factory=list)


class MeResponse(BaseModel):
    id_user: int
    username: str
    roles: List[str] = Field(default_factory=list)


class TariffResponse(BaseModel):
    id_tariff: int
    name_tariff: Optional[str] = None
    price_tariff: Optional[str] = None
    img_tariff: Optional[str] = None

    class Config:
        orm_mode = True


class MessageBase(BaseModel):
    text: str = ""
    sender: str  # "user" or "bot"
    # Имя шаблона поведения модели (режима ответа).
    # Например: "devils_advocate", "analogy", "commit_message", "smm_clickbait".
    template: Optional[str] = None
    image_url: Optional[str] = None
    document_url: Optional[str] = None
    document_name: Optional[str] = None
    document_mime: Optional[str] = None


class MessageCreate(MessageBase):
    # Картинка от клиента (base64 без data: префикса)
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None
    document_base64: Optional[str] = None
    document_name: Optional[str] = None
    document_mime: Optional[str] = None


class Message(MessageBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True


class ChatBase(BaseModel):
    title: str


class ChatCreate(ChatBase):
    pass


class Chat(ChatBase):
    id: int
    created_at: datetime
    last_message: Optional[str] = None
    is_hidden: Optional[bool] = False

    class Config:
        orm_mode = True


class ChatUpdate(BaseModel):
    title: str


class ResponseMode(BaseModel):
    id: int
    template_key: str
    title: str
    system_prompt: str
    sort_order: int = 0
    is_active: bool = True


class ResponseModeCreate(BaseModel):
    template_key: str
    title: str
    system_prompt: str
    sort_order: int = 0
    is_active: bool = True


class ResponseModeUpdate(BaseModel):
    title: Optional[str] = None
    system_prompt: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class AdminServerStatus(BaseModel):
    api_ok: bool = True
    llm_endpoint: str
    llm_model: str
    llm_reachable: bool
    llm_error: Optional[str] = None


class AdminStats(BaseModel):
    users_count: int
    chats_count: int
    user_messages_count: int
    bot_messages_count: int
    response_modes_count: int


class LlmSettings(BaseModel):
    """Параметры, уходящие в OpenAI-совместимый /v1/chat/completions (KoboldCpp и т.д.)."""
    temperature: float
    max_tokens: int
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    repeat_penalty: Optional[float] = None


class LlmSettingsUpdate(BaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    repeat_penalty: Optional[float] = None