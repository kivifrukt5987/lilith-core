"""Тесты RAG-слоя: HashEmbedder и ChromaRag."""

from __future__ import annotations

import pytest

from lilith_core.memory import ChromaRag, HashEmbedder, RagHit


class TestHashEmbedder:
    """Детерминированный эмбеддер без зависимостей."""

    def test_deterministic(self) -> None:
        emb = HashEmbedder(dim=128)
        a = emb.embed(["Кирюша любит чизкейк"])[0]
        b = emb.embed(["Кирюша любит чизкейк"])[0]
        assert a == b

    def test_normalized(self) -> None:
        import math

        vec = HashEmbedder(dim=128).embed(["просто текст"])[0]
        assert math.sqrt(sum(x * x for x in vec)) == pytest.approx(1.0, abs=1e-6)

    def test_similar_texts_closer_than_random(self) -> None:
        emb = HashEmbedder(dim=256)
        base, near, far = emb.embed(
            [
                "Кирюша любит клубничный чизкейк",
                "Кирюша любит клубничный чизкейк очень",
                " OBS меняет сцену на стриме",
            ]
        )

        def cos(x, y):
            return sum(a * b for a, b in zip(x, y))

        assert cos(base, near) > cos(base, far)

    def test_case_and_register_insensitive(self) -> None:
        emb = HashEmbedder(dim=128)
        a = emb.embed(["ПРИВЕТ, Кирюша!"])[0]
        b = emb.embed(["привет, кирюша!"])[0]
        assert a == b

    def test_batch(self) -> None:
        vecs = HashEmbedder(dim=64).embed(["а", "б", "в"])
        assert len(vecs) == 3
        assert all(len(v) == 64 for v in vecs)


@pytest.fixture
def rag(tmp_path):
    """Открытое chroma-хранилище во временной папке."""
    store = ChromaRag(tmp_path / "chroma", collection="test", embedder=HashEmbedder(dim=128))
    chromadb = pytest.importorskip("chromadb")
    assert chromadb is not None
    assert store.open() is True
    yield store
    store.close()


class TestChromaRag:
    """Добавление и поиск записей памяти."""

    def test_add_and_find(self, rag: ChromaRag) -> None:
        rag.add("lilith", "Кирюша любит клубничный чизкейк", {"kind": "fact"})
        rag.add("lilith", "Сервер поднимается start.bat", {"kind": "fact"})

        hits = rag.find("lilith", "что Кирюша любит из десертов?")
        assert hits
        assert isinstance(hits[0], RagHit)
        assert "чизкейк" in hits[0].text

    def test_agent_filter(self, rag: ChromaRag) -> None:
        rag.add("lilith", "мой секрет про чизкейк")
        rag.add("другая", "чужой секрет про чизкейк")

        hits = rag.find("lilith", "секрет про чизкейк", top_k=5)
        assert all(h.meta["agent_id"] == "lilith" for h in hits)
        assert not any("чужой" in h.text for h in hits)

    def test_top_k(self, rag: ChromaRag) -> None:
        for i in range(8):
            rag.add("lilith", f"запись номер {i} про память")
        hits = rag.find("lilith", "запись про память", top_k=3)
        assert len(hits) == 3

    def test_empty_store(self, rag: ChromaRag) -> None:
        assert rag.find("lilith", "пусто вокруг") == []

    def test_blank_queries_and_texts(self, rag: ChromaRag) -> None:
        assert rag.add("lilith", "   ") is False
        assert rag.find("lilith", "  ") == []

    def test_count(self, rag: ChromaRag) -> None:
        rag.add("lilith", "раз")
        rag.add("lilith", "два")
        rag.add("другая", "три")

        assert rag.count() == 3
        assert rag.count("lilith") == 2

    def test_disabled_store_is_silent(self, tmp_path) -> None:
        store = ChromaRag(tmp_path / "c", enabled=False)
        assert store.open() is False
        assert store.available is False
        assert store.add("lilith", "текст") is False
        assert store.find("lilith", "текст") == []
        assert store.count() == 0

    def test_persistence_between_opens(self, tmp_path) -> None:
        store = ChromaRag(tmp_path / "c", collection="persist", embedder=HashEmbedder(dim=64))
        assert store.open()
        store.add("lilith", "переживу закрытие: чизкейк")
        store.close()

        store2 = ChromaRag(tmp_path / "c", collection="persist", embedder=HashEmbedder(dim=64))
        assert store2.open()
        hits = store2.find("lilith", "чизкейк")
        assert hits and "чизкейк" in hits[0].text
        store2.close()
