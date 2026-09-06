import os

import chromadb

from src.utils.config import get_settings

# chromadb phones home telemetry by default; every module that opened its own
# client used to set this individually. One place now that there's one client.
os.environ["ANONYMIZED_TELEMETRY"] = "False"

_client: chromadb.ClientAPI | None = None


def get_chroma_client() -> chromadb.ClientAPI:
    """One PersistentClient per process, shared by filing_agent, ingestion and
    the retriever.

    They used to each open their own client against the same sqlite-backed
    directory. Chroma's own guidance is one client per path per process, and
    concurrent cold requests writing from different threadpool threads through
    separate handles is a lock-contention/corruption risk on the single node
    this runs on.

    A networked store (Qdrant/pgvector) would remove the one-client-per-process
    limit for a multi-replica deployment, but that costs money, so it's
    deliberately out of scope here.
    """
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=get_settings().chroma_persist_dir)
    return _client
