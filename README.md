<p align="center">
  <h1 align="center">GoogleDriveRAG</h1>
  <p align="center">
    Lightweight Graph-RAG engine with built-in Google Drive sync.<br/>
    No external databases. No complex setup. Just <code>pip install</code> and go.
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"/>
  <img src="https://img.shields.io/badge/databases-embedded_only-orange.svg" alt="Embedded Only"/>
</p>

---

## Why GoogleDriveRAG?

Most RAG solutions require you to set up PostgreSQL, Redis, Elasticsearch, Neo4j, or vector databases before you can even start. GoogleDriveRAG takes a different approach:

- **Zero external databases** — SQLite, hnswlib, and NetworkX handle everything in-process
- **Built-in Google Drive sync** — auto-detects new, changed, and deleted files
- **Graph + Vector + Keyword search** — combines Knowledge Graph traversal, vector similarity (HNSW), and BM25 keyword matching for high-quality retrieval
- **Runs on 512MB RAM** — deploy on a $5/month VPS and it just works
- **One YAML file** — configure LLM, embedding, Google Drive, and namespaces in a single file

## How It Works

```
Google Drive ──→ Sync Service ──→ Ingestion Pipeline ──→ Storage (per namespace)
                                                           ├── SQLite (docs, chunks, FTS5)
                                                           ├── hnswlib (vector indexes)
                                                           └── NetworkX (knowledge graph)

User Query ──→ LLM Keyword Extraction ──→ Multi-signal Search ──→ RRF Merge ──→ LLM Answer
                                            ├── Knowledge Graph (local + global)
                                            ├── Vector Similarity (HNSW)
                                            └── BM25 Keyword (FTS5)
```


## Features

### 🔍 High-Quality Retrieval
- **Knowledge Graph** — LLM-extracted entities and relationships, with community detection (Louvain) for global context understanding
- **Vector Search** — HNSW-based similarity search across 3 separate indexes (chunks, entities, relationships)
- **BM25 Keyword Search** — SQLite FTS5 for exact term matching that vector search can miss
- **Reciprocal Rank Fusion** — merges results from all search signals into a single ranked list
- **6 query modes** — `local`, `global`, `hybrid`, `mix`, `naive`, `bypass`

### 📁 Google Drive Integration
- Automatic polling-based sync (configurable interval)
- Smart change detection via `modifiedTime` comparison
- Supports PDF, DOCX, TXT, Markdown, and Google Docs
- Document URLs link back to the original file in Drive
- Per-document reindex via API

### 🗂️ Namespace Isolation
- Each namespace gets its own SQLite database, vector indexes, and knowledge graph
- Physical data isolation — not just logical separation
- Cross-namespace search with RRF merge
- Organize by department, project, or any grouping you need

### 📊 Section-Aware Chunking
- Detects heading hierarchy (Markdown `#`, DOCX Heading styles, PDF font-size analysis)
- Prepends heading path `[H1 > H2 > H3]` to each chunk for context preservation
- Token-based sliding window with configurable overlap

### ⚡ Advanced Retrieval Options
- **Gleaning** — optional second LLM extraction pass for higher entity recall
- **Batch Contextual Enrichment** — LLM generates contextual keywords per chunk batch, improving KG, vector, and BM25 search simultaneously
- **LLM Description Summarization** — condenses verbose entity/relation descriptions
- **Dynamic Token Allocation** — budget-aware context building for entity, relation, and chunk tokens
- **Community Detection** — Louvain-based community search for global queries

### 🛠️ Developer-Friendly
- **Chunk Navigation API** — browse context around any chunk (previous, next, siblings)
- **API Call History** — tracks every LLM/embedding call with tokens, timing, and operation context
- **Built-in Web UI** — search, stats, settings, and API history at `/ui`
- **REST API** — full CRUD for namespaces, documents, chunks, and graph entities
- **Progress Tracking** — real-time indexing progress per document stage

## Quick Start

### Option 1: pip install

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/GoogleDriveRAG.git
cd GoogleDriveRAG

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml — set your LLM API key, embedding API, and Google Drive credentials

# Run
python -m googledriverag serve
```

### Option 2: Docker Compose

```bash
# Clone and configure
git clone https://github.com/YOUR_USERNAME/GoogleDriveRAG.git
cd GoogleDriveRAG
cp config.example.yaml config.yaml
# Edit config.yaml

# Create .env file with your API keys
echo "LLM_API_KEY=your-api-key-here" > .env

# Run
docker compose up
```

Open `http://localhost:8000/ui` in your browser (default credentials: `admin` / `changeme`).

## Configuration

All settings live in a single `config.yaml` file. Environment variables are supported via `${VAR_NAME}` syntax with optional defaults: `${VAR_NAME:default_value}`.

See [`config.example.yaml`](config.example.yaml) for all available options.

### Key Settings

| Setting | Description | Default |
|---|---|---|
| `auth.username` / `password` | HTTP Basic Auth credentials | `admin` / `changeme` |
| `llm.base_url` / `api_key` | OpenAI-compatible LLM endpoint | OpenRouter |
| `llm.enrichment_model` | Model for entity extraction | `gemini-2.0-flash-001` |
| `llm.query_model` | Model for query answering | `gemini-2.5-flash` |
| `embedding.model` | Embedding model | `text-embedding-3-small` |
| `google_drive.credentials_file` | Google Service Account JSON path | `service-account.json` |
| `google_drive.poll_interval_seconds` | Sync interval | `300` (5 min) |
| `namespaces[].folder_ids` | Google Drive folder IDs per namespace | — |
| `retrieval.default_mode` | Default search mode | `mix` |
| `storage.data_dir` | Data directory | `./data` |

### Google Drive Setup

1. Create a Google Cloud project and enable the Google Drive API
2. Create a Service Account and download the JSON key file
3. Share your Google Drive folders with the service account email
4. Set the `credentials_file` path in `config.yaml`
5. Add folder IDs to your namespace configuration

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/stats` | System statistics |
| `POST` | `/query` | Search + generate answer |
| `POST` | `/query/retrieve` | Search only (no LLM generation) |
| `GET` | `/namespaces/` | List namespaces |
| `POST` | `/namespaces/` | Create namespace |
| `PUT` | `/namespaces/{name}` | Update namespace |
| `DELETE` | `/namespaces/{name}` | Delete namespace |
| `POST` | `/namespaces/{name}/sync` | Trigger sync |
| `GET` | `/documents/` | List documents |
| `POST` | `/documents/sync` | Sync all namespaces |
| `DELETE` | `/documents/{id}` | Delete document |
| `GET` | `/chunks/{id}` | Chunk detail |
| `GET` | `/chunks/{id}/context` | Surrounding chunks |
| `GET` | `/chunks/{id}/siblings` | All chunks in same document |
| `GET` | `/graph/entities` | List entities |
| `GET` | `/graph/relationships` | List relationships |
| `GET` | `/ui` | Web UI |

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| HTTP Server | FastAPI + Uvicorn | Async REST API |
| Document Store | SQLite | Documents, chunks, metadata |
| Keyword Search | SQLite FTS5 | BM25 scoring |
| Vector Search | hnswlib | HNSW similarity search |
| Knowledge Graph | NetworkX | Entity/relationship graph |
| Community Detection | python-louvain | Louvain algorithm for global search |
| PDF Parsing | PyMuPDF | Text extraction from PDFs |
| DOCX Parsing | python-docx | Text extraction from Word documents |
| Tokenization | tiktoken | Token counting for chunking |
| Scheduling | APScheduler | Google Drive polling |
| LLM/Embedding | httpx | OpenAI-compatible API client |

## Resource Requirements

| Scale | Documents | RAM | Storage |
|---|---|---|---|
| Small | < 100 | 512 MB | ~100 MB |
| Medium | 100–500 | 1 GB | ~500 MB |
| Large | 500–2000 | 2 GB | ~2 GB |

All storage is local — no network latency to external databases.

## Comparison with Other RAG Solutions

| Feature | GoogleDriveRAG | Typical RAG Stack |
|---|---|---|
| External databases required | ❌ None | PostgreSQL, Redis, Neo4j, etc. |
| Google Drive sync | ✅ Built-in | ❌ Build your own |
| Setup time | Minutes | Hours to days |
| Minimum RAM | 512 MB | 4+ GB |
| Search signals | 3 (KG + Vector + BM25) | Usually 1–2 |
| Namespace isolation | ✅ Physical | Varies |
| Chunk navigation API | ✅ | Rare |

## License

MIT
