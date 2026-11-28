"""Build local course embeddings with ADVISOR_EMBED_MODEL."""

from backend.services.advisor import index
from backend.services.advisor_ollama import build_embeddings


if __name__ == "__main__":
    print(f"Indexed {build_embeddings(index()[0])} new course records.")
