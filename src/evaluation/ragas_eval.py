from src.rag.retriever import retrieve_context


def quick_eval():
    query = "Tesla revenue growth risks"

    docs = retrieve_context(query, company="Tesla")

    print(f"Retrieved {len(docs)} documents")

    for d in docs[:2]:
        print("\n---\n")
        print(d.page_content[:300])


if __name__ == "__main__":
    quick_eval()