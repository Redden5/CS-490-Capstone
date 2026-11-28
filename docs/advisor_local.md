# Local Advisor models

The Advisor combines exact course identities, prerequisite relationships, semantic catalog retrieval, and optional local answer generation. It uses the Ollama HTTP service at `127.0.0.1:11434`. Ollama manages its model files in `C:\Users\robea\.ollama\models`.

## Setup

Install the models with `ollama pull qwen3:4b` and `ollama pull qwen3-embedding:0.6b`. Copy the settings from `backend/advisor.env.example` into the repository root `.env`. Empty model settings disable the corresponding optional feature. Restart FastAPI after changing settings.

Run `.\.venv\Scripts\python.exe -m backend.scripts.index_advisor` from the repository root to build the catalog embedding cache. The command resumes from previously committed batches. Run it again after updating catalog exports. Cache entries include the model name and a hash of the title and description, so changed records require fresh embeddings. Delete `.cache/advisor/embeddings.sqlite3` and rebuild after replacing a model under the same name.

Start the existing FastAPI and Vite servers. Ask “What opens up after CS 210?”, “Which courses teach relational databases?”, or “What are the prerequisites for CS 215?”.

## Design

Prerequisite relationships preserve the source rule text and point out additional conditions. Alternative-course answers return the degree group and its selection instructions. These answers describe recorded relationships. Eligibility remains owned by the existing planning evaluator.

The embedding job indexes catalog titles, descriptions and attributes in batches and stores vectors in a local SQLite cache. Chat embeds only the question and searches current matching cache records. Exact course identities, requested schedule terms, and the existing topic restrictions still constrain retrieval.

The local generator receives the retrieved evidence and source IDs and must return schema-validated JSON. Invalid citations, invented course identities, errors, timeouts, and model contention return the existing cited answer. Source URLs come from backend records. Schedule and personal audit directions remain deterministic. The model can still misstate a supported fact, so citation validity alone does not establish factual accuracy.

Each process permits one local model request at a time. Interactive model requests use a 12-second HTTP timeout and the existing Advisor rate limit. Indexing batches allow 120 seconds for cold startup. Cache construction is an explicit command to keep bulk embedding out of chat requests. The frontend continues to use the existing Advisor layout and API response shape.
