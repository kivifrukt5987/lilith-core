"""LoRA-слот персон (этап 6): интерфейс есть, веса не грузятся.

Решение архитектора **D10-а**: в этапе 6 LoRA — это **prompt-only**. Слот описан
в ``personas/<id>/card.yaml`` (``lora{path, trigger_word, scale, autoload}``),
виден в ``/api/face/personas`` и переключается вместе с персоной, но реально
никакие ``.safetensors`` не загружаются: персону отличает системный промт.

Чтобы этап «LoRA по-настоящему» не переписывал вызывающий код, здесь же —
интерфейс :class:`LoraBackend` и стабы трёх бэкендов (LocalAi / LM Studio /
llama.cpp). :class:`PromptOnlyLora` — рабочий дефолт: он всегда «доступен»,
ничего не грузит и честно сообщает ``state.mode = 'prompt-only'``.

:class:`PersonaLoraManager` — единственная точка, которую зовёт приложение:
``apply(persona)`` при свопе персоны, ``unload()`` при остановке, ``state()``
для диагностики.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from .personas import LoraSpec, Persona

__all__ = [
    "LoraBackend",
    "PromptOnlyLora",
    "LocalAiLora",
    "LMStudioLora",
    "LlamaCppLora",
    "LoraState",
    "PersonaLoraManager",
    "build_lora_manager",
]


@dataclass(slots=True)
class LoraState:
    """Что сейчас применено (для ``state()`` и ``lora.state`` в API)."""

    mode: str = "prompt-only"
    backend: str = "prompt-only"
    persona: str = ""
    path: str = ""
    trigger_word: str = ""
    scale: float = 1.0
    loaded: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Словарь для API/кадров."""
        return {
            "mode": self.mode,
            "backend": self.backend,
            "persona": self.persona or None,
            "path": self.path or None,
            "trigger_word": self.trigger_word or None,
            "scale": self.scale,
            "loaded": self.loaded,
            "reason": self.reason or None,
        }


class LoraBackend:
    """Контракт LoRA-бэкенда.

    Конкретный движок реализует :meth:`apply` / :meth:`unload`; :meth:`available`
    отвечает, можно ли его вообще использовать в этом окружении.
    """

    name = "base"
    mode = "adapter"

    def available(self) -> tuple[bool, str]:
        """Доступен ли бэкенд: ``(ок, причина)``."""
        return False, "не реализован"

    async def apply(self, persona_id: str, spec: LoraSpec) -> LoraState:
        """Применить адаптер персоны."""
        raise NotImplementedError

    async def unload(self) -> None:
        """Снять текущий адаптер."""
        return None

    def state(self) -> LoraState:
        """Текущее состояние."""
        return LoraState(backend=self.name, mode=self.mode)


class PromptOnlyLora(LoraBackend):
    """Дефолт этапа 6: адаптеров нет, персону отличает системный промт."""

    name = "prompt-only"
    mode = "prompt-only"

    def __init__(self) -> None:
        self._state = LoraState(mode=self.mode, backend=self.name, reason="этап 6: LoRA-слот интерфейсный")

    def available(self) -> tuple[bool, str]:
        """Всегда доступен: грузить нечего."""
        return True, ""

    async def apply(self, persona_id: str, spec: LoraSpec) -> LoraState:
        """Запомнить, какая персона «в слоте», и ничего не грузить."""
        self._state = LoraState(
            mode=self.mode,
            backend=self.name,
            persona=persona_id,
            path=spec.path,
            trigger_word=spec.trigger_word,
            scale=spec.scale,
            loaded=False,
            reason="prompt-only: веса не загружаются" if spec.path else "LoRA не настроена",
        )
        logger.debug("LoRA[{}]: prompt-only для персоны '{}'", self.name, persona_id)
        return self._state

    async def unload(self) -> None:
        """Очистить слот."""
        self._state = LoraState(mode=self.mode, backend=self.name, reason="слот пуст")

    def state(self) -> LoraState:
        """Текущее состояние слота."""
        return self._state


class _HttpLoraStub(LoraBackend):
    """Общий стаб HTTP-бэкенда: контракт зафиксирован, реализация за этапом 6."""

    name = "http-stub"
    mode = "adapter"

    def __init__(self, base_url: str = "", api_key_env: str = "") -> None:
        self.base_url = base_url
        self.api_key_env = api_key_env

    def available(self) -> tuple[bool, str]:
        """Стаб недоступен, пока движок не подключён."""
        return False, f"{self.name}: подключение адаптеров появится после выбора движка"

    async def apply(self, persona_id: str, spec: LoraSpec) -> LoraState:
        """Заглушка: возвращает состояние «не загружено»."""
        ok, reason = self.available()
        return LoraState(
            mode=self.mode,
            backend=self.name,
            persona=persona_id,
            path=spec.path,
            scale=spec.scale,
            loaded=False,
            reason=reason if not ok else "",
        )


class LocalAiLora(_HttpLoraStub):
    """Стаб LocalAi (``/v1`` + загрузка адаптеров)."""

    name = "localai"


class LMStudioLora(_HttpLoraStub):
    """Стаб LM Studio (OpenAI-совместимый ``/v1``)."""

    name = "lmstudio"


class LlamaCppLora(_HttpLoraStub):
    """Стаб llama.cpp server (``--lora``)."""

    name = "llama-cpp"


class PersonaLoraManager:
    """Применяет LoRA-слот при свопе персоны.

    Единственный вход для приложения::

        manager = PersonaLoraManager(lora_dir=..., backend=PromptOnlyLora())
        await manager.apply(persona)   # при свопе
        manager.state()                # для /api/face/personas и кадров
    """

    def __init__(
        self,
        lora_dir: str | Path = "",
        backend: LoraBackend | None = None,
        scale: float = 1.0,
        autoload: bool = False,
    ) -> None:
        self.lora_dir = Path(lora_dir) if lora_dir else None
        self.backend = backend or PromptOnlyLora()
        #: Глобальный множитель из ``face.lora_scale`` (перекрывает ``card.lora.scale``).
        self.scale = scale
        #: Глобальный автозапуск из ``face.lora_autoload``.
        self.autoload = autoload
        self._state = self.backend.state()

    # -- разрешение пути -------------------------------------------------------- #
    def resolve_path(self, spec: LoraSpec, persona: Persona | None = None) -> str:
        """Где искать адаптер: путь из карточки, ``lora_dir``, папка персоны."""
        if not spec.path:
            return ""
        candidate = Path(spec.path).expanduser()
        if candidate.is_absolute():
            return str(candidate)
        for base in (self.lora_dir, persona.path / "lora" if persona else None):
            if base is None:
                continue
            joined = Path(base) / candidate
            if joined.is_file():
                return str(joined)
        return str(candidate)

    # -- применение -------------------------------------------------------------- #
    async def apply(self, persona: Persona) -> LoraState:
        """Применить слот персоны (``autoload`` и ``scale`` из конфига имеют приоритет)."""
        spec = persona.card.lora
        effective = LoraSpec(
            path=self.resolve_path(spec, persona),
            trigger_word=spec.trigger_word,
            scale=self.scale if self.scale else spec.scale,
            autoload=self.autoload or spec.autoload,
        )
        ok, reason = self.backend.available()
        if not ok:
            logger.warning("LoRA-бэкенд '{}' недоступен ({}): остаюсь prompt-only", self.backend.name, reason)
            self.backend = PromptOnlyLora()
        self._state = await self.backend.apply(persona.id, effective)
        return self._state

    async def unload(self) -> None:
        """Снять адаптер."""
        await self.backend.unload()
        self._state = self.backend.state()

    def state(self) -> LoraState:
        """Текущее состояние слота."""
        return self._state


def build_lora_manager(settings: Any) -> PersonaLoraManager:
    """Собрать менеджер из конфига (``face.lora_*``).

    Бэкенд выбирается по ``face.lora_backend``; ``prompt-only`` (дефолт этапа 6)
    не требует ничего, остальные приходят стабами и честно сообщают о недоступности.
    """
    face = settings.face
    backends: dict[str, LoraBackend] = {
        "prompt-only": PromptOnlyLora(),
        "localai": LocalAiLora(base_url=getattr(face, "lora_base_url", ""), api_key_env="LILITH_LORA_API_KEY"),
        "lmstudio": LMStudioLora(base_url=getattr(face, "lora_base_url", ""), api_key_env="LILITH_LORA_API_KEY"),
        "llama-cpp": LlamaCppLora(base_url=getattr(face, "lora_base_url", "")),
    }
    backend = backends.get(str(getattr(face, "lora_backend", "prompt-only")), backends["prompt-only"])
    return PersonaLoraManager(
        lora_dir=str(getattr(face, "lora_dir", "") or ""),
        backend=backend,
        scale=float(getattr(face, "lora_scale", 1.0) or 1.0),
        autoload=bool(getattr(face, "lora_autoload", False)),
    )
