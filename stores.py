import os
import re
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

SEARCH_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
KB_TITLE_KEYS = ("title", "name", "question", "heading", "topic")
KB_SOURCE_KEYS = ("source", "url", "path", "file_name", "filename")
KB_IGNORED_KEYS = {"_id", "embedding", "embeddings", "vector", "vectors"}

KB_TEXT_LIMIT = int(os.getenv("KB_TEXT_LIMIT", "2000"))
KB_LIST_ITEMS_LIMIT = int(os.getenv("KB_LIST_ITEMS_LIMIT", "10"))
KB_ENABLE_TIMING_LOGS = os.getenv("KB_ENABLE_TIMING_LOGS", "1") == "1"
KB_PRELOAD = os.getenv("KB_PRELOAD", "1") == "1"
KB_CREATE_INDEX = os.getenv("KB_CREATE_INDEX", "1") == "1"
KB_INCLUDE_ALL_DOCS_IN_PROMPT = os.getenv("KB_INCLUDE_ALL_DOCS_IN_PROMPT", "1") == "1"


def normalize_text_block(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokenize_search_text(text: str) -> List[str]:
    tokens = [token.lower() for token in SEARCH_TOKEN_PATTERN.findall(text)]
    return [token for token in tokens if len(token) > 1]


def build_mongodb_uri(uri_template: str, password: str) -> Tuple[Optional[str], str]:
    template = (uri_template or "").strip()
    if not template:
        return None, "Missing MongoDB URI template."
    if "<db_password>" in template:
        if not password:
            return None, "MongoDB password is blank."
        return template.replace("<db_password>", quote_plus(password)), ""
    return template, ""


def resolve_local_path(path_value: Optional[str]) -> Optional[str]:
    if not path_value:
        return None

    raw_path = Path(path_value).expanduser()
    candidates = [raw_path]

    if not raw_path.is_absolute():
        candidates.append(Path.cwd() / raw_path)
        candidates.append(Path(__file__).resolve().parent / raw_path)

    basename = raw_path.name
    if basename:
        candidates.append(Path(__file__).resolve().parent / "secrets" / basename)
        candidates.append(Path.cwd() / "secrets" / basename)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            return str(resolved)
    return str(raw_path)


def log_kb_timing(operation: str, duration_ms: float, details: str = "") -> None:
    if KB_ENABLE_TIMING_LOGS:
        suffix = f" ({details})" if details else ""
        print(f"[KB TIMING] {operation}: {duration_ms:.1f}ms{suffix}")


class FirebaseStore:
    def __init__(self) -> None:
        self.project_id = os.getenv("FIREBASE_PROJECT_ID")
        self.credentials_path = resolve_local_path(os.getenv("FIREBASE_CREDENTIALS_JSON"))
        self.database_id = os.getenv("FIREBASE_DATABASE_ID", "(default)")
        self.chat_namespace = os.getenv("FIREBASE_CHAT_NAMESPACE", "default")
        self.enabled = bool(self.project_id and self.credentials_path)
        self.last_error = ""
        self._init_client()

    def _init_client(self) -> None:
        if not self.enabled:
            self.client = None
            return
        try:
            from google.cloud import firestore
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path
            )
            self.client = firestore.Client(
                project=self.project_id,
                credentials=credentials,
                database=self.database_id,
            )
            self.last_error = ""
        except Exception:
            self.client = None
            self.last_error = traceback.format_exc()
            if KB_ENABLE_TIMING_LOGS:
                print(self.last_error)

    def log_event(self, event: Dict[str, Any]) -> None:
        if not self.client:
            return
        try:
            self.client.collection("events").add(event)
            self.last_error = ""
        except Exception:
            self.last_error = traceback.format_exc()
            if KB_ENABLE_TIMING_LOGS:
                print(self.last_error)

    def _threads_collection(self):
        if not self.client:
            return None
        return (
            self.client.collection("chat_namespaces")
            .document(self.chat_namespace)
            .collection("threads")
        )

    def save_thread(self, thread_id: str, thread: Dict[str, Any]) -> None:
        collection = self._threads_collection()
        if collection is None:
            return
        payload = {
            "thread_id": thread_id,
            "title": thread.get("title", "Untitled"),
            "messages": thread.get("messages", []),
            "code": thread.get("code", ""),
            "code_status": thread.get("code_status", ""),
            "exec_output": thread.get("exec_output", ""),
            "error": thread.get("error", ""),
            "created_at": thread.get("created_at", ""),
            "updated_at": thread.get("updated_at", ""),
            "order_index": int(thread.get("order_index", 0)),
        }
        try:
            collection.document(thread_id).set(payload)
            self.last_error = ""
        except Exception:
            self.last_error = traceback.format_exc()
            if KB_ENABLE_TIMING_LOGS:
                print(self.last_error)

    def load_threads(self) -> List[Tuple[str, Dict[str, Any]]]:
        collection = self._threads_collection()
        if collection is None:
            return []
        try:
            threads = [(doc.id, doc.to_dict() or {}) for doc in collection.stream()]
            self.last_error = ""
            return threads
        except Exception:
            self.last_error = traceback.format_exc()
            if KB_ENABLE_TIMING_LOGS:
                print(self.last_error)
            return []


class MongoKnowledgeBase:
    def __init__(self) -> None:
        self.client = None
        self.collections: List[Tuple[str, Any]] = []
        self.signature: Optional[Tuple[str, str, Tuple[str, ...]]] = None
        self.preloaded_docs: Dict[str, List[Dict[str, Any]]] = {}
        self.last_error = ""

    def _close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.collections = []
        self.signature = None

    def _parse_collection_names(self, collection_names: str) -> List[str]:
        names = [normalize_text_block(name) for name in collection_names.split(",")]
        aliases = {
            "Apartement": "Apartment",
            "Addvertisement": "Advertisement",
        }
        normalized = [aliases.get(name, name) for name in names if name]
        seen: set[str] = set()
        result: List[str] = []
        for name in normalized:
            if name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result

    def _connect(
        self,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
    ):
        db_name = normalize_text_block(db_name)
        parsed_collection_names = self._parse_collection_names(collection_names)
        if not db_name or not parsed_collection_names:
            self.last_error = "Set MongoDB database and at least one collection before using the Knowledge Base."
            return None

        uri, error = build_mongodb_uri(uri_template, password)
        if error:
            self.last_error = error
            return None

        signature = (uri or "", db_name, tuple(parsed_collection_names))
        if self.collections and self.signature == signature:
            return self.collections

        self._close()
        try:
            from pymongo import MongoClient

            client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            client.admin.command("ping")
            collections = [(name, client[db_name][name]) for name in parsed_collection_names]
            self.client = client
            self.collections = collections
            self.signature = signature
            self.last_error = ""
            try:
                self._ensure_text_indexes()
            except Exception:
                pass
            # Preload all documents into memory only when KB_PRELOAD enabled.
            try:
                if KB_PRELOAD:
                    self._preload_all_documents()
                else:
                    if KB_ENABLE_TIMING_LOGS:
                        print("[KB PRELOAD] disabled by KB_PRELOAD=0")
            except Exception:
                pass
            return collections
        except ModuleNotFoundError:
            self.last_error = "Missing Python package: pymongo. Run `pip install -r requirements.txt`."
        except Exception as exc:
            self.last_error = f"MongoDB connection error: {exc}"

        self._close()
        return None

    def _ensure_text_indexes(self) -> None:
        """Create a wildcard text index on each collection if missing.

        Controlled by `KB_CREATE_INDEX`; set to 0 to skip index creation.
        """
        if not KB_CREATE_INDEX:
            if KB_ENABLE_TIMING_LOGS:
                print("[KB INDEX] creation skipped by KB_CREATE_INDEX=0")
            return
        if not self.collections:
            return
        for name, collection in self.collections:
            try:
                collection.create_index([("$**", "text")])
            except Exception as exc:
                if KB_ENABLE_TIMING_LOGS:
                    print(f"[KB INDEX] failed to ensure text index on {name}: {exc}")

    def _preload_all_documents(self) -> None:
        """Load all documents from each collection into memory for fast scanning.

        Uses a ThreadPoolExecutor to parallelize collection scans to reduce wall-clock time.
        """
        if not self.collections:
            return
        start = time.time()
        total = 0

        def _load(pair: Tuple[str, Any]) -> Tuple[str, int]:
            name, collection = pair
            try:
                docs = list(collection.find({}))
                self.preloaded_docs[name] = docs
                if KB_ENABLE_TIMING_LOGS:
                    print(f"[KB PRELOAD] loaded {len(docs)} documents from {name}")
                return name, len(docs)
            except Exception as exc:
                self.preloaded_docs[name] = []
                if KB_ENABLE_TIMING_LOGS:
                    print(f"[KB PRELOAD] failed to load documents from {name}: {exc}")
                return name, 0

        max_workers = min(len(self.collections), int(os.getenv("KB_PRELOAD_WORKERS", "8")))
        from concurrent.futures import ThreadPoolExecutor, as_completed

        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for pair in self.collections:
                futures.append(executor.submit(_load, pair))
            for fut in as_completed(futures):
                try:
                    _, count = fut.result()
                    total += count
                except Exception:
                    pass

        elapsed_ms = (time.time() - start) * 1000
        log_kb_timing("preload_all_documents", elapsed_ms, f"collections={len(self.collections)}, total_docs={total}")

    def test_connection(
        self,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
    ) -> str:
        collections = self._connect(uri_template, password, db_name, collection_names)
        if collections is None:
            return self.last_error

        try:
            estimated_counts = []
            total_count = 0
            for collection_name, collection in collections:
                estimated_count = collection.estimated_document_count()
                total_count += estimated_count
                estimated_counts.append(f"{collection_name}={estimated_count}")
            return (
                f"Knowledge Base connected to {db_name} across {len(collections)} collection(s): "
                f"{', '.join(estimated_counts)} (total estimated {total_count} document(s))."
            )
        except Exception:
            return f"Knowledge Base connected to {db_name} across {len(collections)} collection(s)."

    def search(
        self,
        query_text: str,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
    ) -> Tuple[List[Dict[str, str]], str]:
        # Simplified search: return all documents formatted for prompt inclusion.
        start = time.time()
        collections = self._connect(uri_template, password, db_name, collection_names)
        if collections is None:
            return [], self.last_error

        # Normalize query_text for status, but we always return full documents
        _ = normalize_text_block(query_text)

        documents = self.get_all_documents_for_prompt()
        elapsed_ms = (time.time() - start) * 1000
        log_kb_timing("search (full-doc return)", elapsed_ms, f"returned={len(documents)}")
        return documents, f"Knowledge Base returned {len(documents)} document(s) from {len(collections)} collection(s)."



    def _label_result(self, result: Dict[str, str], collection_name: str) -> Dict[str, str]:
        labeled = dict(result)
        source = normalize_text_block(labeled.get("source", ""))
        labeled["source"] = f"{collection_name}{' | ' + source if source else ''}"
        return labeled

    def _format_result(self, doc: Dict[str, Any], snippet: str) -> Dict[str, str]:
        fallback_id = str(doc.get("_id", "")).strip()
        title = self._pick_first_value(doc, KB_TITLE_KEYS) or f"Document {fallback_id}".strip()
        source = self._pick_first_value(doc, KB_SOURCE_KEYS) or fallback_id
        return {
            "title": normalize_text_block(title),
            "source": normalize_text_block(source),
            "snippet": normalize_text_block(snippet),
        }

    def _pick_first_value(self, payload: Dict[str, Any], keys: Tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _document_text(self, payload: Any) -> str:
        fragments: List[str] = []
        self._collect_text_fragments(payload, fragments)
        return normalize_text_block(" ".join(fragments))[:KB_TEXT_LIMIT]

    def _collect_text_fragments(self, value: Any, fragments: List[str]) -> None:
        if isinstance(value, str):
            text = normalize_text_block(value)
            if text:
                fragments.append(text)
            return

        if isinstance(value, dict):
            for key, child in value.items():
                if key in KB_IGNORED_KEYS:
                    continue
                self._collect_text_fragments(child, fragments)
            return

        if isinstance(value, list):
            for item in value[:KB_LIST_ITEMS_LIMIT]:
                self._collect_text_fragments(item, fragments)

    def _score_text(self, text: str, query_text: str) -> int:
        if not text:
            return 0
        lowered_text = text.lower()
        lowered_query = query_text.lower()
        score = 0
        if lowered_query in lowered_text:
            score += 5
        for token in tokenize_search_text(query_text):
            if token in lowered_text:
                score += 2 if len(token) > 4 else 1
        return score

    def _snippet_for_query(self, text: str, query_text: str, radius: int = None) -> str:
        if radius is None:
            radius = int(os.getenv("KB_SNIPPET_LIMIT", "150")) // 2  # Default radius based on snippet limit
        if not text:
            return ""
        
    def get_preload_summary(self) -> Dict[str, Any]:
        """Return a diagnostic summary about preloaded documents and text-index presence.

        Useful for debugging whether the app actually read all documents at startup.
        """
        summary: Dict[str, Any] = {"collections": []}
        try:
            for name, collection in self.collections:
                info: Dict[str, Any] = {"collection": name}
                try:
                    docs = self.preloaded_docs.get(name)
                    info["preloaded_count"] = len(docs) if docs is not None else None
                except Exception:
                    info["preloaded_count"] = None

                # Check for a wildcard text index
                has_text_index = False
                try:
                    indexes = collection.index_information()
                    for idx in indexes.values():
                        for key in idx.get("key", []):
                            if isinstance(key, (list, tuple)) and key:
                                if key[0] == "$**" or (len(key) > 1 and key[1] == "text"):
                                    has_text_index = True
                                    break
                        if has_text_index:
                            break
                except Exception:
                    has_text_index = False
                info["has_text_index"] = has_text_index
                try:
                    info["estimated_count"] = collection.estimated_document_count()
                except Exception:
                    info["estimated_count"] = None

                summary["collections"].append(info)
        except Exception:
            pass
        return summary

    def get_all_documents_for_prompt(self) -> List[Dict[str, str]]:
        """Return all documents (across collections) formatted for prompt inclusion.

        This uses preloaded documents when available; otherwise scans collections.
        The returned list contains dicts with `title`, `source`, and `snippet` (full text up to KB_TEXT_LIMIT).
        """
        results: List[Dict[str, str]] = []
        try:
            for name, collection in self.collections:
                docs_iter = None
                preloaded = self.preloaded_docs.get(name)
                if preloaded is not None:
                    docs_iter = preloaded
                else:
                    try:
                        docs_iter = collection.find({})
                    except Exception:
                        docs_iter = []

                for doc in docs_iter:
                    try:
                        text = self._document_text(doc)
                        snippet = text
                        formatted = self._format_result(doc, snippet)
                        # include collection name as source prefix
                        formatted["source"] = f"{name}{' | ' + formatted.get('source','') if formatted.get('source') else ''}"
                        results.append(formatted)
                    except Exception:
                        continue
        except Exception:
            pass
        return results