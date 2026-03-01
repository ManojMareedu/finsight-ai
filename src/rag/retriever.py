import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"

from langchain_chroma import Chroma
from src.rag.embeddings import get_embeddings
from typing import List
from langchain_core.documents import Document


def get_retriever(company_filter: str = None):
    """
    ChromaDB retriever using MMR search.
    """

    vectorstore = Chroma(
        persist_directory="./data/chroma",
        embedding_function=get_embeddings(),
        collection_name="financial_filings",
    )

    search_kwargs = {
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


def retrieve_context(query: str, company: str = None) -> List[Document]:
    retriever = get_retriever(company_filter=company)
    return retriever.invoke(query)