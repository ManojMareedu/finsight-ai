"""Network-free unit test for src/rag/ingestion.py Chroma ID namespacing.

Two companies that would guess the same truncated ticker under the old scheme
(e.g. "American Express" / "American Airlines" -> "AMER") must not collide now
that chunk IDs are namespaced by CIK, and collection.upsert (not .add) is used
so a re-ingest doesn't 500 on duplicate IDs."""

import src.agents.filing_agent as filing_agent_module
import src.rag.ingestion as ingestion_module


class _FakeEmbeddings:
    def embed_documents(self, texts):
        return [[0.0] for _ in texts]


class _FakeCollection:
    def __init__(self):
        self.upserted_ids: list[str] = []

    def upsert(self, ids, embeddings, metadatas, documents):
        self.upserted_ids.extend(ids)

    def add(self, *a, **k):
        raise AssertionError("collection.add should not be called; ingestion must upsert")


class _FakeClient:
    def __init__(self):
        self.collection = _FakeCollection()

    def get_or_create_collection(self, name, metadata=None):
        return self.collection


def test_ids_namespaced_by_cik_and_upsert_used(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(ingestion_module, "get_chroma_client", lambda: fake_client)
    monkeypatch.setattr(ingestion_module, "get_embeddings", lambda: _FakeEmbeddings())
    monkeypatch.setattr(
        ingestion_module, "get_latest_10k_text", lambda cik, max_chars: "Some filing text. " * 50
    )
    # Same guessed ticker prefix, different real CIKs -> must not collide.
    monkeypatch.setattr(
        ingestion_module,
        "get_company_cik",
        lambda name: "0000111" if "Express" in name else "0000222",
    )

    ingestion_module.ingest_company_filing("American Express", "AMER")
    express_ids = list(fake_client.collection.upserted_ids)

    fake_client.collection.upserted_ids.clear()
    ingestion_module.ingest_company_filing("American Airlines", "AMER")
    airlines_ids = list(fake_client.collection.upserted_ids)

    assert express_ids, "expected chunks to be upserted"
    assert set(express_ids).isdisjoint(airlines_ids)
    assert all(i.startswith("0000111_") for i in express_ids)
    assert all(i.startswith("0000222_") for i in airlines_ids)


# --- filing_agent None-into-Chroma-metadata regression -----------------------
# A None metadata value shipped once despite green pytest (chromadb rejects
# None). filing_agent.py is untouched this round; this only tests it.


class _FakeFilingCollection:
    def get(self, where=None, limit=None):
        return {"ids": []}


class _FakeFilingChromaClient:
    def get_or_create_collection(self, name):
        return _FakeFilingCollection()


def test_filing_agent_never_passes_none_ticker_to_ingestion(monkeypatch):
    captured = {}

    def _fake_ingest(company, ticker):
        captured["ticker"] = ticker
        return 1

    monkeypatch.setattr(filing_agent_module, "get_chroma_client", lambda: _FakeFilingChromaClient())
    monkeypatch.setattr(filing_agent_module, "ingest_company_filing", _fake_ingest)
    monkeypatch.setattr(filing_agent_module, "retrieve_context", lambda query, company: [])
    monkeypatch.setattr(filing_agent_module, "resolve_ticker", lambda name, provided: None)

    state = {"company_name": "Unresolvable Company XYZ", "company_ticker": ""}
    filing_agent_module.filing_agent(state)

    assert captured["ticker"] is not None
    assert captured["ticker"] == ""


# --- unified Chroma client (item 19) -----------------------------------------
# filing_agent, ingestion and the retriever each used to open their own
# chromadb.PersistentClient against the same sqlite-backed directory. They
# must now all resolve through the single module-level client in
# src.rag.chroma_client.


def test_single_chroma_client_shared_across_modules(monkeypatch, tmp_path):
    import src.rag.chroma_client as chroma_client
    import src.rag.retriever as retriever_module

    monkeypatch.setattr(chroma_client, "_client", None)
    fake_settings = type("S", (), {"chroma_persist_dir": str(tmp_path)})()
    monkeypatch.setattr(chroma_client, "get_settings", lambda: fake_settings)

    assert filing_agent_module.get_chroma_client is chroma_client.get_chroma_client
    assert ingestion_module.get_chroma_client is chroma_client.get_chroma_client
    assert retriever_module.get_chroma_client is chroma_client.get_chroma_client
    assert chroma_client.get_chroma_client() is chroma_client.get_chroma_client()
