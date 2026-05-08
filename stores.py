import os
import re
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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
KB_INCLUDE_ALL_DOCS_IN_PROMPT = os.getenv("KB_INCLUDE_ALL_DOCS_IN_PROMPT", "0") == "1"
KB_RESULT_LIMIT = max(1, int(os.getenv("KB_RESULT_LIMIT", "5")))
KB_SNIPPET_LIMIT = max(120, int(os.getenv("KB_SNIPPET_LIMIT", "300")))
KB_COLLECTION_FIELD_LIMIT = max(5, int(os.getenv("KB_COLLECTION_FIELD_LIMIT", "40")))
KB_COLLECTION_SAMPLE_VALUES_LIMIT = max(1, int(os.getenv("KB_COLLECTION_SAMPLE_VALUES_LIMIT", "5")))
KB_COLLECTION_OVERVIEW_LIMIT = max(1, int(os.getenv("KB_COLLECTION_OVERVIEW_LIMIT", "3")))
KB_PROFILE_SCAN_DOC_LIMIT = max(0, int(os.getenv("KB_PROFILE_SCAN_DOC_LIMIT", "0")))
FIRESTORE_TRUNCATE_EVENT_PAYLOAD = os.getenv("FIRESTORE_TRUNCATE_EVENT_PAYLOAD", "0") == "1"
FIRESTORE_LOG_TEXT_LIMIT = max(256, int(os.getenv("FIRESTORE_LOG_TEXT_LIMIT", "4000")))
FIRESTORE_LOG_LIST_LIMIT = max(1, int(os.getenv("FIRESTORE_LOG_LIST_LIMIT", "20")))

COLLECTION_CONTEXT_KEYWORDS = {
    "all",
    "chart",
    "charts",
    "collection",
    "columns",
    "compare",
    "dashboard",
    "distribution",
    "field",
    "fields",
    "full",
    "graph",
    "histogram",
    "overview",
    "plot",
    "schema",
    "scatter",
    "stats",
    "summary",
    "bieu do",
    "cot",
    "thong ke",
    "tong quan",
    "truong",
}


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
            payload = self._sanitize_payload(event) if FIRESTORE_TRUNCATE_EVENT_PAYLOAD else event
            self.client.collection("events").add(payload)
            self.last_error = ""
        except Exception:
            self.last_error = traceback.format_exc()
            if KB_ENABLE_TIMING_LOGS:
                print(self.last_error)

    def _sanitize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: self._sanitize_value(value)
            for key, value in payload.items()
        }

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if len(text) <= FIRESTORE_LOG_TEXT_LIMIT:
                return text
            hidden = len(text) - FIRESTORE_LOG_TEXT_LIMIT
            return f"{text[:FIRESTORE_LOG_TEXT_LIMIT]}... [truncated {hidden} chars]"

        if isinstance(value, dict):
            return {
                str(key): self._sanitize_value(child)
                for key, child in list(value.items())[:FIRESTORE_LOG_LIST_LIMIT]
            }

        if isinstance(value, list):
            return [
                self._sanitize_value(item)
                for item in value[:FIRESTORE_LOG_LIST_LIMIT]
            ]

        return value

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
        self.collection_profiles: Dict[str, Dict[str, Any]] = {}
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
        self.collection_profiles = {}

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
        futures = []
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
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
        start = time.time()
        collections = self._connect(uri_template, password, db_name, collection_names)
        if collections is None:
            return [], self.last_error

        normalized_query = normalize_text_block(query_text)
        if not normalized_query:
            return [], "Knowledge Base skipped because the query is empty."

        result_limit = KB_RESULT_LIMIT
        overview_documents = self.get_collection_overviews(normalized_query)
        scored_results: List[Tuple[int, Dict[str, str]]] = []
        max_workers = min(len(collections), int(os.getenv("KB_SEARCH_WORKERS", "8")))

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = [
                executor.submit(
                    self._search_collection,
                    collection_name,
                    collection,
                    normalized_query,
                    result_limit,
                )
                for collection_name, collection in collections
            ]
            for future in as_completed(futures):
                try:
                    scored_results.extend(future.result())
                except Exception as exc:
                    if KB_ENABLE_TIMING_LOGS:
                        print(f"[KB SEARCH] worker failed: {exc}")

        scored_results.sort(
            key=lambda item: (
                -item[0],
                item[1].get("title", ""),
                item[1].get("source", ""),
            )
        )
        documents = overview_documents + [document for _, document in scored_results[:result_limit]]
        elapsed_ms = (time.time() - start) * 1000
        log_kb_timing("search", elapsed_ms, f"returned={len(documents)}")
        if documents:
            overview_suffix = f", plus {len(overview_documents)} collection overview(s)" if overview_documents else ""
            return documents, (
                f"Knowledge Base returned {len(documents)} relevant document(s) "
                f"from {len(collections)} collection(s){overview_suffix}."
            )
        return [], (
            f"Knowledge Base found no relevant documents across {len(collections)} collection(s)."
        )



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

    def _normalize_lookup_name(self, name: str) -> str:
        return normalize_text_block(name).lower().replace("-", " ").replace("_", " ")

    def _resolve_collection_name(self, collection_name: str) -> str:
        target = self._normalize_lookup_name(collection_name)
        for name, _ in self.collections:
            if self._normalize_lookup_name(name) == target:
                return name
        aliases = {
            "apartement": "Apartment",
            "advertisement": "Advertisement",
        }
        alias_target = aliases.get(target)
        if alias_target:
            for name, _ in self.collections:
                if name == alias_target:
                    return name
        return collection_name

    def _matches_collection_name(self, query_text: str, collection_name: str) -> bool:
        lowered_query = self._normalize_lookup_name(query_text)
        lowered_name = self._normalize_lookup_name(collection_name)
        return re.search(rf"(?<!\w){re.escape(lowered_name)}(?!\w)", lowered_query) is not None

    def _requested_collection_names(self, query_text: str) -> List[str]:
        requested: List[str] = []
        for name, _ in self.collections:
            if self._matches_collection_name(query_text, name):
                requested.append(name)
        return requested

    def _needs_collection_context(self, query_text: str, requested_collections: List[str]) -> bool:
        if not requested_collections:
            return False
        lowered_query = self._normalize_lookup_name(query_text)
        if any(keyword in lowered_query for keyword in COLLECTION_CONTEXT_KEYWORDS):
            return True
        return len(requested_collections) == 1

    def _flatten_document_fields(
        self,
        value: Any,
        result: Dict[str, Any],
        prefix: str = "",
        depth: int = 0,
    ) -> None:
        if depth > 2:
            if prefix:
                result[prefix] = value
            return

        if isinstance(value, dict):
            for key, child in value.items():
                if key in KB_IGNORED_KEYS:
                    continue
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                self._flatten_document_fields(child, result, child_prefix, depth + 1)
            return

        if isinstance(value, list):
            if prefix:
                result[prefix] = value
            for item in value[:KB_LIST_ITEMS_LIMIT]:
                if isinstance(item, dict):
                    self._flatten_document_fields(item, result, prefix, depth + 1)
            return

        if prefix:
            result[prefix] = value

    def _sample_display_value(self, value: Any) -> str:
        if isinstance(value, list):
            fragments: List[str] = []
            for item in value[:3]:
                text = normalize_text_block(item)
                if text:
                    fragments.append(text)
            return ", ".join(fragments)
        return normalize_text_block(value)

    def _coerce_number(self, value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None

        text = value.strip().lower()
        if not text:
            return None
        if re.search(r"[a-df-z]", text):
            return None
        text = text.replace(",", "")
        text = text.replace("_", "")
        text = text.replace("%", "")
        if not re.fullmatch(r"[+\-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+\-]?\d+)?", text):
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _build_collection_profile(self, collection_name: str, collection: Any) -> Dict[str, Any]:
        total_docs = 0
        field_stats: Dict[str, Dict[str, Any]] = {}

        for doc in self._iter_collection_documents(collection_name, collection):
            total_docs += 1
            flattened: Dict[str, Any] = {}
            self._flatten_document_fields(doc, flattened)

            for field_name, value in flattened.items():
                if not field_name:
                    continue
                stats = field_stats.setdefault(
                    field_name,
                    {
                        "count": 0,
                        "numeric_count": 0,
                        "sample_counter": Counter(),
                        "min": None,
                        "max": None,
                    },
                )
                stats["count"] += 1

                sample = self._sample_display_value(value)
                if sample:
                    stats["sample_counter"][sample] += 1

                numeric_value = self._coerce_number(value)
                if numeric_value is not None:
                    stats["numeric_count"] += 1
                    stats["min"] = numeric_value if stats["min"] is None else min(stats["min"], numeric_value)
                    stats["max"] = numeric_value if stats["max"] is None else max(stats["max"], numeric_value)

            if KB_PROFILE_SCAN_DOC_LIMIT and total_docs >= KB_PROFILE_SCAN_DOC_LIMIT:
                break

        ordered_fields: List[Tuple[str, Dict[str, Any]]] = []
        for field_name, stats in field_stats.items():
            ordered_fields.append(
                (
                    field_name,
                    {
                        "count": stats["count"],
                        "numeric_count": stats["numeric_count"],
                        "samples": [
                            sample
                            for sample, _ in stats["sample_counter"].most_common(KB_COLLECTION_SAMPLE_VALUES_LIMIT)
                        ],
                        "min": stats["min"],
                        "max": stats["max"],
                    },
                )
            )

        ordered_fields.sort(key=lambda item: (-item[1]["count"], item[0]))
        return {
            "collection": collection_name,
            "total_docs": total_docs,
            "fields": ordered_fields,
        }

    def _get_collection_profile(self, collection_name: str) -> Optional[Dict[str, Any]]:
        resolved_name = self._resolve_collection_name(collection_name)
        if resolved_name in self.collection_profiles:
            return self.collection_profiles[resolved_name]

        for name, collection in self.collections:
            if name != resolved_name:
                continue
            profile = self._build_collection_profile(name, collection)
            self.collection_profiles[name] = profile
            return profile
        return None

    def _format_collection_overview(self, profile: Dict[str, Any]) -> Dict[str, str]:
        total_docs = profile.get("total_docs", 0)
        fields: List[Tuple[str, Dict[str, Any]]] = profile.get("fields", [])
        available_fields = [
            f"{field_name} ({stats['count']}/{total_docs})"
            for field_name, stats in fields[:KB_COLLECTION_FIELD_LIMIT]
        ]
        numeric_lines = [
            f"{field_name}: count={stats['numeric_count']}, min={stats['min']}, max={stats['max']}"
            for field_name, stats in fields
            if stats["numeric_count"] > 0 and stats["numeric_count"] >= max(1, stats["count"] // 2)
        ][: min(10, KB_COLLECTION_FIELD_LIMIT)]
        categorical_lines = [
            f"{field_name}: sample={', '.join(stats['samples'])}"
            for field_name, stats in fields
            if stats["samples"] and stats["numeric_count"] == 0
        ][: min(10, KB_COLLECTION_FIELD_LIMIT)]

        lines = [
            f"Collection: {profile['collection']}",
            f"Rows available: {total_docs}",
            "Available fields: " + (", ".join(available_fields) if available_fields else "(none)"),
        ]
        if numeric_lines:
            lines.append("Numeric-like fields: " + " | ".join(numeric_lines))
        if categorical_lines:
            lines.append("Categorical-like fields: " + " | ".join(categorical_lines))
        lines.append(
            f"For generated Python code, use load_kb_collection('{profile['collection']}') to access the full collection as a pandas DataFrame."
        )
        lines.append(
            f"You can inspect schema details in code with get_kb_collection_schema('{profile['collection']}')."
        )
        return {
            "title": f"{profile['collection']} collection overview",
            "source": f"{profile['collection']} | collection_overview",
            "snippet": "\n".join(lines),
        }

    def get_collection_overviews(self, query_text: str) -> List[Dict[str, str]]:
        requested_collections = self._requested_collection_names(query_text)
        if not self._needs_collection_context(query_text, requested_collections):
            return []

        overviews: List[Dict[str, str]] = []
        for collection_name in requested_collections[:KB_COLLECTION_OVERVIEW_LIMIT]:
            profile = self._get_collection_profile(collection_name)
            if profile is None:
                continue
            overviews.append(self._format_collection_overview(profile))
        return overviews

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

    def _score_document(self, doc: Dict[str, Any], text: str, query_text: str) -> int:
        title = self._pick_first_value(doc, KB_TITLE_KEYS)
        source = self._pick_first_value(doc, KB_SOURCE_KEYS)
        score = self._score_text(text, query_text)
        score += self._score_text(title, query_text) * 3
        score += self._score_text(source, query_text) * 2
        return score

    def _snippet_for_query(self, text: str, query_text: str, radius: int = None) -> str:
        if radius is None:
            radius = KB_SNIPPET_LIMIT // 2
        if not text:
            return ""

        text = normalize_text_block(text)
        lowered_text = text.lower()
        lowered_query = query_text.lower()

        match_start = lowered_text.find(lowered_query) if lowered_query else -1
        match_length = len(lowered_query)

        if match_start < 0:
            for token in sorted(tokenize_search_text(query_text), key=len, reverse=True):
                match_start = lowered_text.find(token)
                if match_start >= 0:
                    match_length = len(token)
                    break

        if match_start < 0:
            return text[:KB_SNIPPET_LIMIT]

        start = max(0, match_start - radius)
        end = min(len(text), match_start + match_length + radius)
        snippet = text[start:end].strip()
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(text):
            snippet = f"{snippet}..."
        return snippet[:KB_SNIPPET_LIMIT + 6]

    def _iter_collection_documents(self, collection_name: str, collection: Any):
        preloaded = self.preloaded_docs.get(collection_name)
        if preloaded is not None:
            return preloaded
        try:
            return collection.find({})
        except Exception:
            return []

    def _search_collection(
        self,
        collection_name: str,
        collection: Any,
        query_text: str,
        result_limit: int,
    ) -> List[Tuple[int, Dict[str, str]]]:
        matches: List[Tuple[int, Dict[str, str]]] = []
        for doc in self._iter_collection_documents(collection_name, collection):
            try:
                text = self._document_text(doc)
                if not text:
                    continue
                score = self._score_document(doc, text, query_text)
                if score <= 0:
                    continue
                snippet = self._snippet_for_query(text, query_text)
                result = self._label_result(self._format_result(doc, snippet), collection_name)
                matches.append((score, result))
            except Exception:
                continue

        matches.sort(
            key=lambda item: (
                -item[0],
                item[1].get("title", ""),
                item[1].get("source", ""),
            )
        )
        return matches[:result_limit]

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

    def get_collection_records(
        self,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
        collection_name: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        collections = self._connect(uri_template, password, db_name, collection_names)
        if collections is None:
            return []

        resolved_name = self._resolve_collection_name(collection_name)
        for name, collection in collections:
            if name != resolved_name:
                continue
            docs = list(self._iter_collection_documents(name, collection))
            if limit is not None:
                return docs[: max(0, limit)]
            return docs
        return []

    def get_collection_dataframe_records(
        self,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
        collection_name: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        docs = self.get_collection_records(
            uri_template=uri_template,
            password=password,
            db_name=db_name,
            collection_names=collection_names,
            collection_name=collection_name,
            limit=limit,
        )
        rows: List[Dict[str, Any]] = []
        for doc in docs:
            row = dict(doc)
            if "_id" in row:
                row["_id"] = str(row["_id"])
            rows.append(row)
        return rows

    def describe_collection(
        self,
        uri_template: str,
        password: str,
        db_name: str,
        collection_names: str,
        collection_name: str,
    ) -> Dict[str, Any]:
        collections = self._connect(uri_template, password, db_name, collection_names)
        if collections is None:
            return {}

        resolved_name = self._resolve_collection_name(collection_name)
        profile = self._get_collection_profile(resolved_name)
        if profile is None:
            return {}

        fields_payload: List[Dict[str, Any]] = []
        for field_name, stats in profile.get("fields", [])[:KB_COLLECTION_FIELD_LIMIT]:
            fields_payload.append(
                {
                    "name": field_name,
                    "count": stats["count"],
                    "numeric_count": stats["numeric_count"],
                    "samples": list(stats["samples"]),
                    "min": stats["min"],
                    "max": stats["max"],
                }
            )

        return {
            "collection": profile.get("collection", resolved_name),
            "total_docs": profile.get("total_docs", 0),
            "fields": fields_payload,
        }

    def get_all_documents_for_prompt(self) -> List[Dict[str, str]]:
        """Return all documents (across collections) formatted for prompt inclusion.

        This uses preloaded documents when available; otherwise scans collections.
        The returned list contains dicts with `title`, `source`, and `snippet` (full text up to KB_TEXT_LIMIT).
        """
        results: List[Dict[str, str]] = []
        try:
            for name, collection in self.collections:
                for doc in self._iter_collection_documents(name, collection):
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
