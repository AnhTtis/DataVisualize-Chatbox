# Gradio Demo Bot

This is a Gradio demo with:
- Chat UI with Gemini and conversation list
- MongoDB-backed Knowledge Base for optional retrieval context
- Model page (placeholder)
- Human approval before executing AI-generated code
- Firestore storage for chat history, code, and outputs

## Setup

1. Create a Python environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create `.env` and fill values.
4. Start the app:
   ```bash
   python app.py
   ```
   Or:
   ```bash
   python -m app
   ```
   Do not use `python -m app.py`; Python treats `app.py` as a module path there and it fails.

## Knowledge Base Setup

The app can preload documents across configured collections, rank them by relevance for each query, and send only the top matches to the model. To ensure fast and reliable retrieval:

1. Index creation

   The application attempts to create necessary MongoDB text indexes at startup when the Knowledge Base is configured. No separate script is required.

   If you prefer to create indexes manually, you can run similar logic against your MongoDB collections (for example using the MongoDB shell or a one-time script).

2. Set MongoDB credentials in `.env`:
   ```
   MONGODB_URI_TEMPLATE=mongodb+srv://user:<db_password>@cluster.mongodb.net/
   MONGODB_DB_NAME=your_database
   MONGODB_COLLECTION_NAME=Advertisement,Apartment
   ```

3. The Knowledge Base will:
   - Optionally preload all documents in all specified collections at startup
   - Search multiple collections in parallel for speed
   - Rank documents by relevance and include only the top matches in the chat prompt
   - Add collection overview context when a question explicitly targets a whole collection such as `House`
   - Provide answers even when the Knowledge Base has no matching documents

### Performance Tuning (Optional)

If needed, customize in `.env`:
```
KB_RESULT_LIMIT=5                 # Number of KB results to include in prompt
KB_SNIPPET_LIMIT=300             # Max characters per snippet (affects display)
KB_TEXT_LIMIT=2000                # Max characters to extract from each document
KB_LIST_ITEMS_LIMIT=10            # Max nested array items to process
KB_PRELOAD=1                      # Preload documents into memory at startup
KB_INCLUDE_ALL_DOCS_IN_PROMPT=0   # Force legacy full-KB prompt mode
KB_COLLECTION_FIELD_LIMIT=40      # Max fields to show in collection overview context
FIRESTORE_TRUNCATE_EVENT_PAYLOAD=0 # Keep full event logs; set to 1 only if you need truncation
KB_ENABLE_TIMING_LOGS=1           # Log search performance metrics
```

Performance metrics are printed to console when `KB_ENABLE_TIMING_LOGS=1`:
`[KB TIMING] search: 156.2ms (returned=5)`

### Notes

- Firebase storage uses a service account JSON in `FIREBASE_CREDENTIALS_JSON`.
- Knowledge Base can preload documents across all specified collections, but only top-ranked results are added to the prompt by default.
- Generated Python code can use `load_kb_collection("House")`, `get_kb_collection_schema("House")`, and `list_kb_collections()` for collection-wide analysis and charts.
- Set `MONGODB_DB_NAME` and `MONGODB_COLLECTION_NAME` in `.env` before using the Knowledge Base.

 - Firebase storage uses a service account JSON in `FIREBASE_CREDENTIALS_JSON`.
 - Set `MONGODB_DB_NAME` and `MONGODB_COLLECTION_NAME` in `.env` before using the Knowledge Base.
- Chat threads are persisted in Firestore under `chat_namespaces/{FIREBASE_CHAT_NAMESPACE}/threads`. If `FIREBASE_CHAT_NAMESPACE` is omitted, the app uses `default`.
- The app resolves service-account JSON paths from the configured path or from the local `secrets/` folder by filename.
- Firestore must exist in the configured Firebase project. If your project uses a non-default database, set `FIREBASE_DATABASE_ID`; otherwise create the default Firestore database first.
- Code execution is a demo and should be sandboxed for production.
