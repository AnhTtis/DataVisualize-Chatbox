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

The app scans all documents across all collections when searching the Knowledge Base. To ensure fast and reliable retrieval:

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
   - Scan all documents in all specified collections (the app preloads documents at startup)
   - Search multiple collections in parallel for speed
   - Internal ranking considers all documents; the chat prompt includes the top 5 matches by default
   - Provide answers even when the Knowledge Base has no matching documents

### Performance Tuning (Optional)

If needed, customize in `.env`:
```
KB_TOP_RESULTS=5                  # Number of KB results to include in prompt
KB_SNIPPET_LIMIT=150              # Max characters per snippet (affects display)
KB_TEXT_LIMIT=2000                # Max characters to extract from each document
KB_LIST_ITEMS_LIMIT=10            # Max nested array items to process
KB_SEARCH_TIMEOUT_SECONDS=30      # Max time for KB search operation
KB_ENABLE_TIMING_LOGS=1           # Log search performance metrics
```

Performance metrics are printed to console when `KB_ENABLE_TIMING_LOGS=1`:
```
[KB TIMING] search (text index): 156.2ms (found=12, display=5)
[KB TIMING] _search_by_scan: 234.5ms (scanned=5000, results=12)
```

### Notes

- Firebase storage uses a service account JSON in `FIREBASE_CREDENTIALS_JSON`.
- Knowledge Base scans all documents across all specified collections. Use `create_kb_index.py` to create text indexes for faster retrieval.
- Set `MONGODB_DB_NAME` and `MONGODB_COLLECTION_NAME` in `.env` before using the Knowledge Base.

 - Firebase storage uses a service account JSON in `FIREBASE_CREDENTIALS_JSON`.
 - Set `MONGODB_DB_NAME` and `MONGODB_COLLECTION_NAME` in `.env` before using the Knowledge Base.
- Chat threads are persisted in Firestore under `chat_namespaces/{FIREBASE_CHAT_NAMESPACE}/threads`. If `FIREBASE_CHAT_NAMESPACE` is omitted, the app uses `default`.
- The app resolves service-account JSON paths from the configured path or from the local `secrets/` folder by filename.
- Firestore must exist in the configured Firebase project. If your project uses a non-default database, set `FIREBASE_DATABASE_ID`; otherwise create the default Firestore database first.
- Code execution is a demo and should be sandboxed for production.
