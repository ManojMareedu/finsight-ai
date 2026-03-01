import logging
import os

import chromadb
from langchain.text_splitter import RecursiveCharacterTextSplitter

from src.rag.embeddings import get_embeddings
from src.utils.data_fetchers import get_company_cik, get_latest_10k_text

os.environ["ANONYMIZED_TELEMETRY"] = "False"

logger = logging.getLogger(__name__)

# Chunk configuration
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
    separators=["\n\n", "\n", ". ", " ", ""],
)


def ingest_company_filing(company_name: str, ticker: str, chroma_dir: str) -> int:
    """
    Fetch SEC filing → chunk → embed → store in ChromaDB.
    Returns number of chunks stored.
    """

    logger.info(f"Fetching filing for {company_name}")

    cik = get_company_cik(company_name)
    if not cik:
        logger.warning("CIK not found")
        return 0

    raw_text = get_latest_10k_text(cik)
    if not raw_text:
        logger.warning("No filing text")
        return 0

    # Split into chunks
    chunks = splitter.create_documents(
        texts=[raw_text],
        metadatas=[{
            "company": company_name,
            "ticker": ticker,
            "source": "SEC_10K",
            "cik": cik,
        }]
    )

    logger.info(f"Created {len(chunks)} chunks")

    # ChromaDB
    client = chromadb.PersistentClient(path=chroma_dir)

    collection = client.get_or_create_collection(
        name="financial_filings",
        metadata={"hnsw:space": "cosine"},
    )

    embeddings_model = get_embeddings()

    texts = [c.page_content for c in chunks]
    metadatas = [c.metadata for c in chunks]

    embeddings = embeddings_model.embed_documents(texts)

    ids = [f"{ticker}_{i}" for i in range(len(texts))]

    collection.add(
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
        ids=ids,
    )

    return len(chunks)
