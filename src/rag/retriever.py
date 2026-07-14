import os

os.environ["ANONYMIZED_TELEMETRY"] = "False"

from typing import Any, Dict, List, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.rag.embeddings import get_embeddings
from src.utils.config import get_settings


def get_retriever(company_filter: Optional[str] = None):
    """
    ChromaDB retriever using MMR search.

    Reads the persist directory from settings so retrieval and ingestion
    always point at the same store (e.g. /data/chroma in the container).
    """

    vectorstore = Chroma(
        persist_directory=get_settings().chroma_persist_dir,
        embedding_function=get_embeddings(),
        collection_name="financial_filings",
    )

    # Explicit typing avoids mypy conflicts
    search_kwargs: Dict[str, Any] = {
        "k": 6,
        "fetch_k": 20,
        "lambda_mult": 0.7,
    }

    if company_filter:
        search_kwargs["filter"] = {"company": company_filter}

    return vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs=search_kwargs,
    )


def retrieve_context(
    query: str,
    company: Optional[str] = None
) -> List[Document]:

    retriever = get_retriever(company_filter=company)
    return retriever.invoke(query)
