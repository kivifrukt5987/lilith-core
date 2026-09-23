"""Подсистема конфигурации LILITH-CORE.

Конфигурация собирается из четырёх источников, по убыванию приоритета:

1. переменные окружения (``LILITH_<РАЗДЕЛ>__<ПОЛЕ>``);
2. файл ``.env``;
3. YAML-файл (``config/config.yaml`` или путь из переменной ``LILITH_CONFIG``);
4. значения по умолчанию, описанные в pydantic-моделях этого модуля.

Правило простое: чем ближе к машине/запуску — тем важнее. Секреты живут в ``.env``
и в переменных окружения, «форма» проекта — в ``config.yaml``.

Типизация и валидация — на ``pydantic`` / ``pydantic-settings``, поэтому опечатка
в конфиге падает сразу и с внятным сообщением, а не через час работы в рантайме.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

__all__ = [
    "AppSettings",
    "ServerSettings",
    "LoggingSettings",
    "BrainDefaults",
    "BrainProfile",
    "BrainSettings",
    "ResolvedProfile",
    "MemorySettings",
    "VoiceSettings",
    "FaceSettings",
    "HandsSettings",
    "BridgesSettings",
    "StreamSettings",
    "FeatureFlags",
    "Settings",
    "YamlConfigSource",
    "find_config_file",
    "load_settings",
    "get_settings",
    "reset_settings_cache",
    "PROJECT_ROOT",
]

#: Корень репозитория (…/Локальная Лилит). Используется для поиска конфига по умолчанию.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

#: Имя переменной окружения, задающей путь к YAML-конфигу.
CONFIG_ENV_VAR = "LILITH_CONFIG"

#: Префикс всех переменных окружения проекта.
ENV_PREFIX = "LILITH_"

#: Разделитель для вложенных полей: ``LILITH_BRAIN__MODEL`` -> ``brain.model``.
ENV_NESTED_DELIMITER = "__"


# --------------------------------------------------------------------------- #
#  Секции конфигурации
# --------------------------------------------------------------------------- #
class _Section(BaseModel):
    """Базовая секция: строгая валидация, запрещены неизвестные ключи."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AppSettings(_Section):
    """Общие сведения о приложении и о персонаже."""

    name: str = "LILITH-CORE"
    env: str = "dev"
    debug: bool = False
    #: Как Лилит обращается к пользователю.
    user_name: str = "Кирюша"
    #: Имя персонажа (подставляется в шаблон ``persona.md``).
    persona_name: str = "Лилит"
    #: Идентификатор агента по умолчанию (память и сессии ключуются им, этап 3+).
    agent_id: str = "lilith"
    #: Каталог персон: personas/<id>/{persona.md,model.vrm,fallback.*} (этап 5 v2).
    personas_dir: str = "personas"
    #: Путь к файлу системного промта.
    persona_path: str = "src/lilith_core/persona/persona.md"


class ServerSettings(_Section):
    """HTTP/WebSocket-сервер."""

    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    ws_path: str = "/ws"
    ws_max_message_size: int = Field(default=65536, ge=1024)
    ws_heartbeat_sec: float = Field(default=15.0, gt=0)
    ws_reconnect_timeout_sec: float = Field(default=30.0, gt=0)


class LoggingSettings(_Section):
    """Настройки loguru."""

    level: str = "INFO"
    dir: str = "logs"
    file_name: str = "lilith.log"
    rotation: str = "10 MB"
    retention: str = "14 days"
    compression: str = "zip"
    console: bool = True
    backtrace: bool = True
    diagnose: bool = False
    #: **Хотфикс 0.6.4.** Access-лог HTTP-запросов, чей путь начинается с одного
    #: из этих префиксов. Пустой список = access-лог выключен.
    #:
    #: Зачем: uvicorn у нас поднимается с ``access_log=False`` (иначе консоль
    #: залита поллингом веб-панели), и на приёмке F7 не было видно главного —
    #: что Unity вообще **не** стучался за телом. Теперь каждый
    #: ``GET /api/face/personas/<id>/model.vrm`` оставляет строку в логе.
    access_log_prefixes: list[str] = Field(default_factory=lambda: ["/api/face/"])

    @property
    def file_path(self) -> Path:
        """Полный путь к файлу лога."""
        return Path(self.dir) / self.file_name


class BrainDefaults(_Section):
    """Общие параметры для всех профилей мозга (наследуются, если профиль молчит)."""

    provider: str = "openai_compatible"
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "local-model"
    temperature: float = Field(default=0.8, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    top_p: float = Field(default=0.95, gt=0.0, le=1.0)
    request_timeout_sec: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    stream: bool = True
    history_max_messages: int = Field(default=40, ge=1)
    #: Грубый бюджет контекста в токенах (для урезания истории, этап 2).
    context_window: int = Field(default=8192, ge=512)


class BrainProfile(_Section):
    """Именованный профиль модели.

    Профиль = конкретная модель на конкретном сервере (порт/имя/ключ/параметры).
    Все поля ``None`` наследуются из :class:`BrainDefaults`, поэтому новый
    экспериментальный модельный слот добавляется тремя строками в ``config.yaml``::

        profiles:
          experiment:
            model: qwen4-2b-something-Q4_K_M
            base_url: http://127.0.0.1:1237/v1
    """

    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    request_timeout_sec: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    stream: bool | None = None
    history_max_messages: int | None = Field(default=None, ge=1)
    #: Персональная переменная окружения с ключом (иначе берётся общая ``brain.api_key_env``).
    api_key_env: str | None = None
    #: Человекочитаемая пометка: какая это модель, квант, зачем.
    note: str = ""


@dataclass(slots=True)
class ResolvedProfile:
    """Эффективные параметры профиля после слияния с ``defaults``."""

    name: str
    provider: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    top_p: float
    request_timeout_sec: float
    max_retries: int
    stream: bool
    history_max_messages: int
    api_key_env: str
    note: str

    def resolved_api_key(self) -> str:
        """Ключ из переменной окружения ``api_key_env`` (локальным серверам не нужен)."""
        return os.getenv(self.api_key_env, "") or ""

    def masked(self) -> dict[str, Any]:
        """Сводка профиля без секретов — для логов и ``/healthz``."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": self.stream,
            "history_max_messages": self.history_max_messages,
            "note": self.note,
        }


class BrainSettings(_Section):
    """Слой «мозг»: реестр профилей OpenAI-совместимых endpoint'ов (этап 2).

    Зачем реестр, а не одна модель: компаньон болтает быстрой малышкой, код
    отдают тяжёлой модели или облаку, скриншоты смотрит мультимодальная, а
    память саммаризирует самая дешёвая. Переключение — сменой
    ``default_profile`` или полем ``data.profile`` в WS-сообщении ``chat``.
    """

    #: Профиль по умолчанию.
    default_profile: str = "chat"
    defaults: BrainDefaults = Field(default_factory=BrainDefaults)
    profiles: dict[str, BrainProfile] = Field(
        default_factory=lambda: {"chat": BrainProfile()}
    )
    api_key: SecretStr = SecretStr("")
    #: Имя переменной окружения с общим запасным ключом.
    api_key_env: str = "LILITH_BRAIN_API_KEY"
    system_prompt_extra: str = ""

    @model_validator(mode="after")
    def _validate_profiles(self) -> "BrainSettings":
        """Проверяет, что реестр не пуст и профиль по умолчанию существует."""
        if not self.profiles:
            raise ValueError("brain.profiles: нужен хотя бы один профиль модели")
        if self.default_profile not in self.profiles:
            raise ValueError(
                f"brain.default_profile '{self.default_profile}' не найден среди "
                f"профилей: {', '.join(sorted(self.profiles))}"
            )
        return self

    def profile_names(self) -> list[str]:
        """Имена всех профилей по алфавиту."""
        return sorted(self.profiles)

    def resolve(self, name: str | None = None) -> ResolvedProfile:
        """Сливает ``defaults`` с профилем и возвращает эффективные параметры.

        :param name: имя профиля; ``None`` — профиль по умолчанию.
        :raises KeyError: если профиля нет в реестре.
        """
        name = name or self.default_profile
        profile = self.profiles.get(name)
        if profile is None:
            raise KeyError(
                f"профиль мозга '{name}' не найден; доступны: {', '.join(self.profile_names())}"
            )
        d = self.defaults
        return ResolvedProfile(
            name=name,
            provider=profile.provider or d.provider,
            base_url=profile.base_url or d.base_url,
            model=profile.model or d.model,
            temperature=d.temperature if profile.temperature is None else profile.temperature,
            max_tokens=d.max_tokens if profile.max_tokens is None else profile.max_tokens,
            top_p=d.top_p if profile.top_p is None else profile.top_p,
            request_timeout_sec=(
                d.request_timeout_sec
                if profile.request_timeout_sec is None
                else profile.request_timeout_sec
            ),
            max_retries=d.max_retries if profile.max_retries is None else profile.max_retries,
            stream=d.stream if profile.stream is None else profile.stream,
            history_max_messages=(
                d.history_max_messages
                if profile.history_max_messages is None
                else profile.history_max_messages
            ),
            api_key_env=profile.api_key_env or self.api_key_env,
            note=profile.note,
        )

    def resolved_api_key(self, name: str | None = None) -> str:
        """Ключ API: явный из конфига, иначе из окружения профиля/общего.

        Локальным серверам (LM Studio, llama.cpp) ключ не нужен, поэтому пустое
        значение не считается ошибкой.
        """
        value = self.api_key.get_secret_value()
        if value:
            return value
        return self.resolve(name).resolved_api_key()

    def masked(self) -> dict[str, Any]:
        """Конфиг мозга для безопасного вывода в лог (ключ замаскирован)."""
        key = self.resolved_api_key()
        shown = f"{key[:3]}…{key[-2:]} (len={len(key)})" if len(key) > 6 else ("не задан" if not key else "***")
        return {
            "default_profile": self.default_profile,
            "api_key": shown,
            "profiles": {
                name: self.resolve(name).masked() for name in self.profile_names()
            },
        }


class MemorySettings(_Section):
    """Слой «память»: журнал на aiosqlite + RAG на chromadb (этап 3)."""

    db_path: str = "data/lilith.db"
    journal_max_messages: int = Field(default=5000, ge=1)
    summary_model: str = ""
    rag_enabled: bool = True
    chroma_path: str = "data/chroma"
    chroma_collection: str = "lilith_memory"
    #: ``hash`` = HashEmbedder без моделей; иначе имя sentence-transformers (CPU).
    embedding_model: str = "hash"
    top_k: int = Field(default=6, ge=1)
    #: Авто-саммаризация журнала; выключается из программы (шестерёнка/эндпоинт).
    auto_summarize: bool = True
    #: Порог саммаризации: каждые N сообщений. Меняется из программы на лету.
    summarize_every_n: int = Field(default=40, ge=1)
    #: Куда сохраняются рантайм-настройки памяти (переживают перезапуск).
    runtime_settings_path: str = "data/runtime_settings.json" 


class VoiceSettings(_Section):
    """Слои «уши» (STT/VAD) и «горло» (TTS) — этап 4."""

    stt_enabled: bool = False
    #: Реестр ушей: faster-whisper | whisper-cpp | vosk | mock (горячая замена).
    stt_backend: str = "faster-whisper"
    stt_model: str = "Systran/faster-whisper-small"
    stt_device: str = "cuda"
    stt_compute_type: str = "float16"
    stt_language: str = "ru"
    vad_enabled: bool = True
    vad_backend: str = "silero"          # silero | energy (energy = без зависимостей)
    vad_model: str = "silero_vad"
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)  # silero: вероятность речи
    #: energy-vad: порог RMS кадра (шкала громкости, не вероятности).
    vad_energy_threshold: float = Field(default=0.02, ge=0.0, le=1.0)
    push_to_talk_key: str = "ctrl+space"
    tts_enabled: bool = False
    tts_backend: str = "silero"
    tts_voice: str = "kseniya"
    tts_edge_voice: str = "ru-RU-DmitryNeural"
    tts_sample_rate: int = Field(default=48000, ge=8000)
    tts_speed: float = Field(default=1.0, gt=0)
    tts_device: str = "cuda"
    #: Профиль голоса по умолчанию (ключ voice.profiles).
    default_voice: str = "lilith"
    #: Именованные голоса: backend/voice/reference/speed/note/extra.
    profiles: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {"lilith": {"backend": "silero", "voice": "kseniya"}}
    )
    #: Папка zero-shot-голосов: voices/<имя>/reference.wav.
    voices_dir: str = "voices"
    #: Манифест паков моделей и корень, куда их ставить.
    packs_manifest: str = "models/packs.yaml"
    packs_root: str = "" 


class FaceSettings(_Section):
    """Слой «лицо»: продюсер для Unity, VTuber Studio + VMC, группы (этапы 5–6)."""

    vtuber_studio_enabled: bool = False
    vtuber_studio_url: str = "ws://localhost:8001"
    vtuber_studio_plugin: str = "LILITH-CORE"
    vtuber_studio_developer: str = "Kiryusha"
    vtuber_studio_token_env: str = "LILITH_VTS_TOKEN"
    vmc_enabled: bool = False
    vmc_host: str = "127.0.0.1"
    vmc_port: int = Field(default=39539, ge=1, le=65535)
    reconnect_delay_sec: float = Field(default=3.0, gt=0)
    emotion_default: str = "neutral"
    #: Эмоция -> хоткей VTuber Studio (id из настроек аватара).
    hotkeys: dict[str, str] = Field(default_factory=dict)
    #: Догадываться по ключевым словам, если модель не поставила теги.
    keyword_fallback: bool = True

    # --- этап 6: продюсер лица для внешних клиентов (Unity/OBS) ---------------- #
    #: Путь WS-сокета продюсера (``/ws/unity`` — алиас, решение A6.1-б).
    producer_path: str = "/ws/face/producer"
    #: Путь WS-сокета групповых сцен (E3).
    group_path: str = "/ws/group"
    #: A1-а/A2: raw PCM int16 mono, 2048 байт на чанк, частота фиксируется здесь.
    producer_sample_rate: int = Field(default=24000, ge=8000, le=192000)
    producer_chunk_bytes: int = Field(default=2048, ge=256)
    #: TTL эмоции по умолчанию; сброс делает Unity (A3.3).
    emotion_ttl_ms: int = Field(default=4000, ge=0)
    #: B1-б: VRM в веб-панели остаётся, но выключен — основной путь теперь Unity.
    web_vrm_enabled: bool = False

    # --- этап 6: LoRA-слот персон (D10-а: prompt-only) -------------------------- #
    #: prompt-only | localai | lmstudio | llama-cpp (всё кроме первого — стабы).
    lora_backend: str = "prompt-only"
    #: Корень для относительных путей адаптеров из ``card.yaml: lora.path``.
    lora_dir: str = ""
    #: Глобальный множитель адаптера (перекрывает ``lora.scale`` персоны).
    lora_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    #: Грузить адаптер автоматически при свопе персоны.
    lora_autoload: bool = False
    #: OpenAI-совместимый endpoint для HTTP-бэкендов LoRA (стабы).
    lora_base_url: str = ""

    # --- этап 6: групповые сцены (E1/E4) ---------------------------------------- #
    #: Потолок участников групповой сцены.
    group_max_participants: int = Field(default=4, ge=1, le=16)
    #: Файл рассадки (слоты/позиции поверх ``face.yaml`` каждой персоны).
    group_file: str = ""

    def resolved_token(self) -> str:
        """Токен VTuber Studio из переменной окружения."""
        return os.getenv(self.vtuber_studio_token_env, "") or ""


class HandsSettings(_Section):
    """Слой «руки»: инструменты + Human-in-the-Loop (этап 6)."""

    confirm_timeout_sec: float = Field(default=25.0, gt=0)
    require_confirmation: bool = True
    allow_vitals: bool = True
    allow_browser: bool = True
    allow_obs: bool = True
    browser_headless: bool = False
    obs_host: str = "127.0.0.1"
    obs_port: int = Field(default=4455, ge=1, le=65535)
    obs_password_env: str = "LILITH_OBS_PASSWORD"

    def resolved_obs_password(self) -> str:
        """Пароль obs-websocket из переменной окружения."""
        return os.getenv(self.obs_password_env, "") or ""


class BridgesSettings(_Section):
    """Слои «мосты»: Discord и VK (этап 7)."""

    discord_enabled: bool = False
    discord_token_env: str = "LILITH_DISCORD_TOKEN"
    discord_command_prefix: str = "!"
    vk_enabled: bool = False
    vk_token_env: str = "LILITH_VK_TOKEN"
    vk_group_id: int = 0

    def resolved_discord_token(self) -> str:
        """Токен Discord-бота из переменной окружения."""
        return os.getenv(self.discord_token_env, "") or ""

    def resolved_vk_token(self) -> str:
        """Токен VK-бота из переменной окружения."""
        return os.getenv(self.vk_token_env, "") or ""


class StreamSettings(_Section):
    """Слой «стрим»: Twitch-триггеры и сцены OBS (этап 8)."""

    twitch_enabled: bool = False
    twitch_token_env: str = "LILITH_TWITCH_TOKEN"
    twitch_channel: str = ""
    obs_scene_on_chat: str = "Just Chatting"
    obs_scene_on_stream: str = "Lilith Live"
    autostart: bool = False

    def resolved_twitch_token(self) -> str:
        """OAuth-токен Twitch из переменной окружения."""
        return os.getenv(self.twitch_token_env, "") or ""


class FeatureFlags(_Section):
    """Флаги подсистем: что реально включено в этом запуске."""

    web_panel: bool = True
    echo_mode: bool = True
    brain_enabled: bool = False
    memory_enabled: bool = False
    voice_enabled: bool = False
    face_enabled: bool = False
    hands_enabled: bool = False
    bridges_enabled: bool = False

    def enabled(self) -> list[str]:
        """Список названий включённых флагов — удобно для логов и ``/healthz``."""
        return sorted(name for name, value in self.model_dump().items() if value)


class Settings(BaseSettings):
    """Корневая конфигурация приложения."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    brain: BrainSettings = Field(default_factory=BrainSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    face: FaceSettings = Field(default_factory=FaceSettings)
    hands: HandsSettings = Field(default_factory=HandsSettings)
    bridges: BridgesSettings = Field(default_factory=BridgesSettings)
    stream: StreamSettings = Field(default_factory=StreamSettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    # -- удобные производные ------------------------------------------------ #
    @property
    def persona_file(self) -> Path:
        """Абсолютный путь к ``persona.md``."""
        return resolve_path(self.app.persona_path)

    @property
    def is_production(self) -> bool:
        """True, если ``app.env == 'prod'``."""
        return self.app.env.strip().lower() == "prod"

    def public_dict(self) -> dict[str, Any]:
        """Конфиг для вывода в лог/healthz без секретов."""
        data = self.model_dump(mode="json")
        data["brain"] = self.brain.masked()
        data.pop("app", None)
        return data

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
        **_: Any,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Порядок источников по убыванию приоритета.

        ``pydantic-settings`` применяет источники так, что **первый** указанный
        имеет наивысший приоритет. Поэтому:

        ``init`` → ``переменные окружения`` → ``.env`` → ``config.yaml`` → ``значения по умолчанию``.
        """
        yaml_path = resolve_config_path()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls, yaml_path),
            file_secret_settings,
        )


# --------------------------------------------------------------------------- #
#  YAML-источник
# --------------------------------------------------------------------------- #
class YamlConfigSource(PydanticBaseSettingsSource):
    """Источник настроек из YAML-файла.

    Файл может отсутствовать — тогда источник просто ничего не добавляет,
    и в силу вступают значения по умолчанию.
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_file: Path | str | None) -> None:
        super().__init__(settings_cls)
        self.yaml_file: Path | None = Path(yaml_file) if yaml_file else None
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        """Читает и кэширует содержимое YAML-файла."""
        if self._data is not None:
            return self._data
        data: dict[str, Any] = {}
        if self.yaml_file and self.yaml_file.is_file():
            with self.yaml_file.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Конфиг {self.yaml_file} должен содержать словарь верхнего уровня, "
                    f"получено: {type(raw).__name__}"
                )
            data = raw
        self._data = data
        return data

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        """Возвращает значение поля из YAML (используется базовым классом)."""
        value = self._load().get(field_name)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Полный словарь значений источника."""
        return dict(self._load())


# --------------------------------------------------------------------------- #
#  Поиск файла конфигурации
# --------------------------------------------------------------------------- #
def resolve_path(raw: str | Path, base: Path | None = None) -> Path:
    """Превращает строку из конфига в путь.

    * абсолютный путь возвращается как есть;
    * если задан ``base`` — относительный путь разрешается строго от него
      (существование файла не проверяется: каталог может быть ещё не создан);
    * иначе применяется эвристика: сначала текущая рабочая директория, затем
      корень репозитория. Это позволяет запускать сервер как из корня проекта,
      так и из произвольного места.
    """
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path

    if base is not None:
        return Path(base).expanduser() / path

    candidate = Path.cwd() / path
    if candidate.exists():
        return candidate

    from_root = PROJECT_ROOT / path
    if from_root.exists():
        return from_root
    return candidate


def resolve_config_path(explicit: str | Path | None = None) -> Path | None:
    """Определяет, какой YAML-конфиг использовать.

    Порядок: явный аргумент → переменная ``LILITH_CONFIG`` → ``./config/config.yaml``
    → ``<корень репозитория>/config/config.yaml``. Если ничего не найдено — ``None``.
    """
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_absolute() else (Path.cwd() / path).resolve()

    from_env = os.getenv(CONFIG_ENV_VAR)
    if from_env:
        path = Path(from_env).expanduser()
        return path if path.is_absolute() else (Path.cwd() / path).resolve()

    for candidate in (Path.cwd() / "config" / "config.yaml", PROJECT_ROOT / "config" / "config.yaml"):
        if candidate.is_file():
            return candidate.resolve()
    return None


def find_config_file(explicit: str | Path | None = None) -> Path | None:
    """Публичный алиас :func:`resolve_config_path` (удобен в тестах и CLI)."""
    path = resolve_config_path(explicit)
    if path is None:
        return None
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
#  Загрузка настроек
# --------------------------------------------------------------------------- #
def load_settings(
    config_path: str | Path | None = None,
    *,
    env_file: str | Path | None = ".env",
    use_cache: bool = False,
) -> Settings:
    """Собирает :class:`Settings` из всех источников.

    :param config_path: явный путь к YAML (иначе ищется автоматически).
    :param env_file: путь к ``.env``; ``None`` — не читать.
    :param use_cache: вернуть закэшированный экземпляр, если он уже есть.
    :raises FileNotFoundError: если ``config_path`` задан явно, но файл не найден.
    """
    if use_cache:
        cached = _SETTINGS_CACHE.get("value")
        if cached is not None:
            return cached  # type: ignore[return-value]

    if config_path is not None:
        resolved = Path(config_path).expanduser()
        if not resolved.is_absolute():
            resolved = (Path.cwd() / resolved).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Конфигурационный файл не найден: {resolved}")
        os.environ[CONFIG_ENV_VAR] = str(resolved)

    dotenv = Path(env_file) if env_file else None
    if dotenv is not None and not dotenv.is_file():
        dotenv = PROJECT_ROOT / dotenv.name
        if not dotenv.is_file():
            dotenv = None

    settings = Settings(_env_file=dotenv)  # type: ignore[call-arg]
    _SETTINGS_CACHE["value"] = settings
    _SETTINGS_CACHE["path"] = resolve_config_path(config_path)
    return settings


_SETTINGS_CACHE: dict[str, Any] = {"value": None, "path": None}


@lru_cache(maxsize=1)
def _cached_default() -> Settings:
    """Ленивый синглтон настроек по умолчанию."""
    return load_settings()


def get_settings(reload: bool = False) -> Settings:
    """Возвращает глобальный экземпляр настроек.

    :param reload: пересобрать конфигурацию (например, после правки ``config.yaml``).
    """
    if reload or _SETTINGS_CACHE["value"] is None:
        return load_settings()
    return _SETTINGS_CACHE["value"]  # type: ignore[return-value]


def reset_settings_cache() -> None:
    """Сбрасывает кэш настроек (нужно в тестах)."""
    _SETTINGS_CACHE["value"] = None
    _SETTINGS_CACHE["path"] = None
    _cached_default.cache_clear()


def config_source_info() -> dict[str, Any]:
    """Справка о том, откуда взялась конфигурация, — для логов и ``/healthz``."""
    return {
        "config_env_var": CONFIG_ENV_VAR,
        "config_path": str(_SETTINGS_CACHE.get("path") or resolve_config_path() or "не найден (значения по умолчанию)"),
        "project_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd()),
    }
