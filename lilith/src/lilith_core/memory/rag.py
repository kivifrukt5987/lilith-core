"""RAG-слой памяти: эмбеддеры и векторное хранилище (chromadb).

Два эмбеддера на выбор:

* :class:`HashEmbedder` — детерминированный, без зависимостей и без моделей:
  символьные n-граммы → signe-хеш по фиксированным корзинам → L2-нормализация.
  Дефолт: работает из коробки, не трогает GPU и не качает модели.
* :class:`SentenceEmbedder` — sentence-transformers на CPU (качество выше,
  требует ``pip install -e .[memory]`` и первой загрузки модели).

Хранилище — :class:`ChromaRag` (PersistentClient). Если chromadb не установлен,
слой вежливо отключается с предупреждением, а не роняет ядро.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

__all__ = ["Embedder", "HashEmbedder", "SentenceEmbedder", "ChromaRag", "RagHit"]


class Embedder(Protocol):
    """Протокол эмбеддера: текст -> вектор float'ов фиксированной размерности."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторы для списка текстов."""
        ...


class HashEmbedder:
    """Детерминированный символьный хеш-эмбеддер без зависимостей.

    Не семантика, а «отпечаток близости»: общие n-граммы дают близкие векторы.
    Достаточно для поиска по своим же записям памяти; ноль веса, ноль сети.
    """

    def __init__(self, dim: int = 384, ngram: int = 3) -> None:
        self.dim = dim
        self.ngram = ngram

    @staticmethod
    def _normalize(text: str) -> str:
        text = unicodedata.normalize("NFKC", text).casefold()
        return "".join(ch for ch in text if not unicodedata.combining(ch))

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        clean = self._normalize(text)
        grams = [clean[i : i + self.ngram] for i in range(max(0, len(clean) - self.ngram + 1))] or [clean]
        for gram in grams:
            h = 0
            for ch in gram:
                h = (h * 131 + ord(ch)) & 0xFFFFFFFF
            idx = h % self.dim
            sign = 1.0 if (h >> 16) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторы текстов (детерминированно между процессами и запусками)."""
        return [self._vector(text) for text in texts]


class SentenceEmbedder:
    """sentence-transformers на CPU (ленивый импорт и ленивая загрузка модели)."""

    def __init__(self, model_name: str, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device
        self._model = None
        self.dim = 384

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # лениво: тяжёлая зависимость

            logger.info("RAG: загружаю эмбеддер {} на {}…", self.model_name, self.device)
            self._model = SentenceTransformer(self.model_name, device=self.device)
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Векторы текстов через sentence-transformers."""
        model = self._load()
        vectors = model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, row)) for row in vectors]


@dataclass(slots=True)
class RagHit:
    """Найдённая запись памяти."""

    text: str
    distance: float
    meta: dict[str, Any]


class _ChromaEmbeddingFunction:
    """Мостик: наш Embedder -> интерфейс embedding_function хromadb."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002 - сигнатура chroma
        return self._embedder.embed(list(input))


class ChromaRag:
    """Векторное хранилище памяти на chromadb (PersistentClient)."""

    def __init__(
        self,
        path: str | Path,
        collection: str = "lilith_memory",
        embedder: Embedder | None = None,
        enabled: bool = True,
    ) -> None:
        self.path = Path(path)
        self.collection_name = collection
        self.embedder = embedder or HashEmbedder()
        self.enabled = enabled
        self._client = None
        self._collection = None

    # -- жизненный цикл ------------------------------------------------------ #
    def open(self) -> bool:
        """Открывает хранилище. False, если chromadb недоступен (слой отключён)."""
        if not self.enabled:
            logger.info("RAG выключен конфигом (memory.rag_enabled=false)")
            return False
        try:
            import chromadb  # лениво: тяжёлая зависимость
        except ImportError as exc:
            logger.warning(
                "RAG отключён: chromadb не установлен ({}). Поставь: pip install -e .[memory]",
                exc.__class__.__name__,
            )
            self.enabled = False
            return False

        self.path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.path))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("RAG открыт: {} (коллекция {})", self.path, self.collection_name)
        return True

    def close(self) -> None:
        """Закрывает хранилище (chromadb сам держит персистентность)."""
        self._client = None
        self._collection = None

    @property
    def available(self) -> bool:
        """True, если хранилище открыто и работает."""
        return self._collection is not None

    # -- операции ------------------------------------------------------------- #
    def add(self, agent_id: str, text: str, meta: dict[str, Any] | None = None) -> bool:
        """Добавляет запись памяти. meta.chroma не любит None — чистим."""
        if not self.available or not text.strip():
            return False
        clean_meta = {
            "agent_id": agent_id,
            **{k: str(v) for k, v in (meta or {}).items() if v is not None},
        }
        vector = self.embedder.embed([text])[0]
        existing = self._collection.count()
        self._collection.add(
            ids=[f"m{existing}_{agent_id}_{abs(hash(text)) & 0xFFFFFF:06x}"],
            embeddings=[vector],
            documents=[text],
            metadatas=[clean_meta],
        )
        return True

    def find(self, agent_id: str, query: str, top_k: int = 6) -> list[RagHit]:
        """Ищет близкие записи агента. Пустой список, если слой выключен."""
        if not self.available or not query.strip():
            return []
        vector = self.embedder.embed([query])[0]
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=max(1, top_k),
            where={"agent_id": agent_id},
            include=["documents", "distances", "metadatas"],
        )
        hits: list[RagHit] = []
        documents = (result.get("documents") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        for text, distance, meta in zip(documents, distances, metadatas):
            hits.append(RagHit(text=str(text), distance=float(distance), meta=dict(meta or {})))
        return hits

    def count(self, agent_id: str | None = None) -> int:
        """Число записей (всех или по агенту)."""
        if not self.available:
            return 0
        if agent_id is None:
            return int(self._collection.count())
        got = self._collection.get(where={"agent_id": agent_id}, include=[])
        return len(got.get("ids") or [])
