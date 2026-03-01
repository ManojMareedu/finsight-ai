from functools import lru_cache
from typing import List

from langchain.embeddings.base import Embeddings
from sentence_transformers import SentenceTransformer


class LocalEmbeddings(Embeddings):
    """
    Local sentence-transformers embedding model.
    Runs fully local → no API cost.
    """

    def __init__(self, model_name="sentence-transformers/all-MiniLM-L6-v2"):
        self.model = SentenceTransformer(model_name)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False
        )
        return embeddings.tolist()

    def embed_query(self, text: str) -> List[float]:
        embedding = self.model.encode([text], show_progress_bar=False)
        return embedding[0].tolist()


@lru_cache(maxsize=1)
def get_embeddings() -> LocalEmbeddings:
    """Load once, reuse everywhere."""
    return LocalEmbeddings()
