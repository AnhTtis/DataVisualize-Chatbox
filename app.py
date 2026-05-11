import io
import html
import math
import mimetypes
import os
import re
import sys
import tempfile
import traceback
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

import gradio as gr
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google import genai
from PIL import Image

from app_mode import build_property_model_page
from stores import (
    FirebaseStore,
    KB_ENABLE_TIMING_LOGS,
    KB_INCLUDE_ALL_DOCS_IN_PROMPT,
    MongoKnowledgeBase,
    SEARCH_TOKEN_PATTERN,
    build_mongodb_uri,
)

load_dotenv()


APP_ROOT = Path(__file__).resolve().parent
SECRETS_DIR = APP_ROOT / "secrets"
DOCS_DIR = APP_ROOT / "docs"
SKILLS_DIR = DOCS_DIR / "skills"
TOOLS_DIR = DOCS_DIR / "tools"

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AUTO_INSTALL_WHITELIST = os.getenv("AUTO_INSTALL_WHITELIST", "0") == "1"
PREINSTALL_LIBS = [
    "numpy",
    "pandas",
    "matplotlib",
    "seaborn",
    "scikit-learn",
    "scipy",
    "plotly",
    "requests",
]

DEFAULT_MONGODB_URI_TEMPLATE = os.getenv(
    "MONGODB_URI_TEMPLATE",
    "mongodb+srv://visualizer:<db_password>@propertyanalysis.clzm37k.mongodb.net/",
)
DEFAULT_MONGODB_PASSWORD = os.getenv("MONGODB_PASSWORD", "")
DEFAULT_MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "")
DEFAULT_MONGODB_COLLECTION_NAME = os.getenv("MONGODB_COLLECTION_NAME", "")

ATTACHMENT_PROMPT_TEXT_LIMIT = max(1200, int(os.getenv("ATTACHMENT_PROMPT_TEXT_LIMIT", "5000")))
ATTACHMENT_STORED_TEXT_LIMIT = max(300, int(os.getenv("ATTACHMENT_STORED_TEXT_LIMIT", "1600")))
THREAD_ATTACHMENT_CONTEXT_LIMIT = max(2000, int(os.getenv("THREAD_ATTACHMENT_CONTEXT_LIMIT", "6000")))
MAX_INLINE_FILE_BYTES = max(512_000, int(os.getenv("MAX_INLINE_FILE_BYTES", "7500000")))
MAX_THREAD_FILE_CONTEXT_ITEMS = max(1, int(os.getenv("MAX_THREAD_FILE_CONTEXT_ITEMS", "5")))

TEXT_FILE_EXTENSIONS = {
    ".csv",
    ".json",
    ".txt",
    ".md",
    ".log",
    ".py",
    ".sql",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xlsm", ".xls"}
DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MULTIMODAL_MIME_TYPES = {"application/pdf"}

APP_CSS = """
.sidebar-card {
  border: 1px solid #d7dde8;
  border-radius: 12px;
  padding: 10px;
  background: #fafcff;
}
.file-history {
  max-height: 240px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.file-chip {
  border: 1px solid #d9e1ef;
  border-radius: 8px;
  padding: 10px 12px;
  background: white;
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  box-sizing: border-box;
  position: relative;
  transition: all 0.2s ease;
}
.file-chip:hover {
  background: #f5f8ff;
  border-color: #a8d8ff;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.file-chip-info {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}
.file-chip strong {
  display: block;
  margin: 0;
  font-size: 13px;
  word-break: break-word;
}
.file-chip small {
  color: #5d6b82;
  font-size: 12px;
  white-space: nowrap;
  margin-top: 2px;
}
.file-chip-actions {
  display: flex;
  gap: 6px;
  margin-left: 10px;
  opacity: 0;
  transition: opacity 0.2s ease;
}
.file-chip:hover .file-chip-actions {
  opacity: 1;
}
.file-action-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 16px;
  padding: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #5d6b82;
  transition: color 0.2s ease;
  border-radius: 4px;
}
.file-action-btn:hover {
  color: #2563eb;
  background: rgba(37, 99, 235, 0.1);
}
.file-action-btn.download:hover {
  color: #059669;
}
.file-action-btn.download:hover {
  background: rgba(5, 150, 105, 0.1);
}
.image-history-grid {
    border: 1px solid #d7dde8;
    border-radius: 12px;
    background: #fafcff;
    padding: 8px;
    max-height: 260px;
    overflow-y: auto;
}
.image-history-grid .grid-wrap {
    gap: 8px;
}
"""


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text_block(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def trim_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    hidden = len(text) - limit
    return f"{text[:limit].rstrip()}... [truncated {hidden} chars]"


def tokenize_search_text(text: str) -> List[str]:
    tokens = [token.lower() for token in SEARCH_TOKEN_PATTERN.findall(text)]
    return [token for token in tokens if len(token) > 1]


def log_kb_timing(operation: str, duration_ms: float, details: str = "") -> None:
    if KB_ENABLE_TIMING_LOGS:
        suffix = f" ({details})" if details else ""
        print(f"[KB TIMING] {operation}: {duration_ms:.1f}ms{suffix}")


def merge_errors(*messages: str) -> str:
    seen: set[str] = set()
    ordered: List[str] = []
    for message in messages:
        cleaned = str(message or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return "\n\n".join(ordered)


def format_store_error() -> str:
    if not store.last_error:
        return ""
    last_line = store.last_error.strip().splitlines()[-1].strip()
    if "does not exist for project" in store.last_error and "database" in store.last_error.lower():
        return (
            f"Firebase error: {last_line}. Create a Firestore database for project "
            f"'{store.project_id}' or set FIREBASE_DATABASE_ID to an existing database."
        )
    if "MongoDB GridFS unavailable" in store.last_error:
        return (
            "MongoDB media storage is temporarily unavailable. "
            "Assets are cached locally so they still appear in the current UI session."
        )
    return f"Storage error: {last_line}"


def format_bytes(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes or 0)))
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(str(path))
    return mime_type or "application/octet-stream"


def is_text_like(path: Path, mime_type: str) -> bool:
    return path.suffix.lower() in TEXT_FILE_EXTENSIONS or mime_type.startswith("text/")


def read_text_file(path: Path) -> str:
    encodings = ["utf-8", "utf-8-sig", "cp1258", "latin-1"]
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding, errors="ignore")
        except Exception:
            continue
    return ""


def extract_text_from_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(page.strip() for page in pages if page.strip())
    except Exception:
        return ""


def extract_text_from_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")
        root = ET.fromstring(document_xml)
        texts = [node.text for node in root.iter() if node.text]
        return "\n".join(texts)
    except Exception:
        return ""


def extract_text_from_spreadsheet(path: Path) -> str:
    try:
        if path.suffix.lower() == ".xls":
            return ""
        sheets = pd.read_excel(path, sheet_name=None)
        blocks: List[str] = []
        for sheet_name, frame in sheets.items():
            blocks.append(f"[Sheet] {sheet_name}")
            blocks.append(frame.head(40).to_csv(index=False))
        return "\n\n".join(blocks)
    except Exception:
        return ""


def extract_text_from_upload(path_value: str) -> str:
    path = Path(path_value)
    suffix = path.suffix.lower()
    mime_type = guess_mime_type(path)

    if is_text_like(path, mime_type):
        return read_text_file(path)
    if suffix in PDF_EXTENSIONS:
        return extract_text_from_pdf(path)
    if suffix in DOCX_EXTENSIONS:
        return extract_text_from_docx(path)
    if suffix in SPREADSHEET_EXTENSIONS:
        return extract_text_from_spreadsheet(path)
    return ""


def should_send_as_multimodal_part(path: Path, mime_type: str, size_bytes: int) -> bool:
    if size_bytes > MAX_INLINE_FILE_BYTES:
        return False
    if mime_type.startswith("image/"):
        return True
    if mime_type in MULTIMODAL_MIME_TYPES:
        return True
    return False


def build_instruction_catalog() -> Dict[str, Dict[str, str]]:
    catalog: Dict[str, Dict[str, str]] = {}
    doc_paths = {
        "operations": DOCS_DIR / "CHATBOX_OPERATIONS.md",
        "data_visualization": SKILLS_DIR / "data_visualization.md",
        "document_reasoning": SKILLS_DIR / "document_reasoning.md",
        "general_assistant": SKILLS_DIR / "general_assistant.md",
        "external_data_handling": SKILLS_DIR / "external_data_handling.md",
        "chatbox_tools": TOOLS_DIR / "chatbox_tools.md",
        "storage_backends": TOOLS_DIR / "storage_backends.md",
    }
    for key, path in doc_paths.items():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            catalog[key] = {"path": str(path), "content": text}
    return catalog

INSTRUCTION_CATALOG = build_instruction_catalog()


def select_instruction_keys(user_text: str) -> List[str]:
    lowered = (user_text or "").lower()
    keys = ["operations", "chatbox_tools"]

    data_viz_markers = [
        "chart",
        "plot",
        "graph",
        "dashboard",
        "visual",
        "biểu đồ",
        "vẽ",
        "trực quan",
        "màu",
        "scatter",
        "histogram",
    ]
    document_markers = [
        "file",
        "upload",
        "pdf",
        "csv",
        "excel",
        "document",
        "tệp",
        "tập tin",
        "ảnh",
    ]
    external_data_markers = [
        "web",
        "scrape",
        "api",
        "crawl",
        "fetch",
        "request",
        "url",
        "website",
        "data source",
        "nguồn dữ liệu",
        "cào dữ liệu",
    ]

    if any(marker in lowered for marker in data_viz_markers):
        keys.append("data_visualization")
    if any(marker in lowered for marker in document_markers):
        keys.append("document_reasoning")
    if any(marker in lowered for marker in external_data_markers):
        keys.append("external_data_handling")
    if "storage" in lowered or "firebase" in lowered or "mongo" in lowered:
        keys.append("storage_backends")
    if len(keys) == 2:
        keys.append("general_assistant")

    deduped: List[str] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen or key not in INSTRUCTION_CATALOG:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def build_instruction_context(user_text: str) -> str:
    sections: List[str] = []
    for key in select_instruction_keys(user_text):
        entry = INSTRUCTION_CATALOG.get(key)
        if not entry:
            continue
        title = key.replace("_", " ").title()
        sections.append(f"[{title}]\n{entry['content']}")
    return "\n\n".join(sections)


def build_client() -> Optional[genai.Client]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def extract_code(text: str) -> Optional[str]:
    if "```" not in text:
        return None
    matches = re.findall(r"```(?:python)?\n([\s\S]*?)```", text)
    if not matches:
        return None
    return "\n\n".join(match.strip() for match in matches if match.strip())


def format_knowledge_base_context(documents: List[Dict[str, str]]) -> str:
    sections: List[str] = []
    for idx, document in enumerate(documents, start=1):
        lines = [f"[KB {idx}]"]
        if document.get("title"):
            lines.append(f"Title: {document['title']}")
        if document.get("source"):
            lines.append(f"Source: {document['source']}")
        if document.get("snippet"):
            lines.append(f"Content: {document['snippet']}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def lookup_knowledge_base(
    query_text: str,
    uri_template: str,
    password: str,
    db_name: str,
    collection_name: str,
) -> Tuple[str, str, List[Dict[str, str]]]:
    if KB_INCLUDE_ALL_DOCS_IN_PROMPT:
        collections = knowledge_base._connect(uri_template, password, db_name, collection_name)
        if collections is None:
            return "", knowledge_base.last_error, []
        documents = knowledge_base.get_all_documents_for_prompt()
        return (
            format_knowledge_base_context(documents),
            f"Knowledge Base included all documents from {len(collections)} collection(s).",
            documents,
        )

    documents, status = knowledge_base.search(
        query_text=query_text,
        uri_template=uri_template,
        password=password,
        db_name=db_name,
        collection_names=collection_name,
    )
    return format_knowledge_base_context(documents), status, documents


def render_prompt(
    config: Dict[str, Any],
    knowledge_base_context: str = "",
    instruction_context: str = "",
) -> str:
    parts = ["You are a helpful assistant for data analysis, visualization, and document-aware chat."]
    behavior = config.get("behavior", "")
    if behavior:
        parts.append(f"Behavior: {behavior}")
    parts.append("Use uploaded files and thread file history whenever they are relevant to the user's question.")
    parts.append("If relevant Knowledge Base context is provided, use it first and say when you are relying on it.")
    parts.append("If the Knowledge Base is unavailable, irrelevant, or incomplete, still answer helpfully using your own reasoning.")
    parts.append("Do not refuse general questions just because they are outside the Knowledge Base.")
    parts.append("If you generate code, wrap it in a fenced block using triple backticks.")
    parts.append(
        "For collection-wide analysis or charts, generated Python code can call "
        "`load_kb_collection(collection_name)`, `get_kb_collection_schema(collection_name)`, and `list_kb_collections()`."
    )
    parts.append(
        "For uploaded files, generated Python code can call `list_thread_files()`, `get_thread_file_path(file_name_or_id)`, "
        "and `load_thread_file(file_name_or_id)`."
    )
    parts.append("When plotting, prefer the provided `plt` object instead of importing matplotlib manually.")
    parts.append(
        "The execution environment already provides common aliases like `np`, `pd`, `re`, `os`, `json`, `math`, and `Path`."
    )
    if instruction_context:
        parts.append("Operational Guidance:")
        parts.append(instruction_context)
    if knowledge_base_context:
        parts.append("Knowledge Base Context:")
        parts.append(knowledge_base_context)
    return "\n".join(parts)


def build_contents(
    messages: List[Dict[str, Any]],
    user_text: str,
    attachment_parts: Optional[Sequence[Any]] = None,
    max_messages: int = 12,
) -> List[Any]:
    contents: List[Any] = []
    start = max(0, len(messages) - max_messages)
    for message in messages[start:]:
        role = message.get("role", "user")
        content = str(message.get("model_content", message.get("content", "")))
        api_role = "user" if role == "user" else "model"
        contents.append(
            genai.types.Content(
                role=api_role,
                parts=[genai.types.Part.from_text(text=content)],
            )
        )

    parts: List[Any] = [genai.types.Part.from_text(text=user_text)]
    if attachment_parts:
        parts.extend(attachment_parts)
    contents.append(genai.types.Content(role="user", parts=parts))
    return contents


def load_kb_collection(collection_name: str, limit: Optional[int] = None):
    rows = knowledge_base.get_collection_dataframe_records(
        uri_template=DEFAULT_MONGODB_URI_TEMPLATE,
        password=DEFAULT_MONGODB_PASSWORD,
        db_name=DEFAULT_MONGODB_DB_NAME,
        collection_names=DEFAULT_MONGODB_COLLECTION_NAME,
        collection_name=collection_name,
        limit=limit,
    )
    return pd.DataFrame(rows)


def get_kb_collection_schema(collection_name: str) -> Dict[str, Any]:
    return knowledge_base.describe_collection(
        uri_template=DEFAULT_MONGODB_URI_TEMPLATE,
        password=DEFAULT_MONGODB_PASSWORD,
        db_name=DEFAULT_MONGODB_DB_NAME,
        collection_names=DEFAULT_MONGODB_COLLECTION_NAME,
        collection_name=collection_name,
    )


def list_kb_collections() -> List[str]:
    return knowledge_base._parse_collection_names(DEFAULT_MONGODB_COLLECTION_NAME)


def code_requests_plotting(code: str) -> bool:
    lowered = (code or "").lower()
    plot_markers = [
        "matplotlib",
        "plt.",
        "pyplot",
        "seaborn",
        "sns.",
        ".plot(",
        ".hist(",
        ".bar(",
        ".scatter(",
        ".line(",
        ".boxplot(",
    ]
    return any(marker in lowered for marker in plot_markers)


class MatplotlibUnavailableProxy:
    def __init__(self, error_message: str) -> None:
        self.error_message = error_message

    def __getattr__(self, name: str):
        raise RuntimeError(self.error_message)


def load_matplotlib_pyplot() -> Tuple[Optional[Any], str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt, ""
    except Exception as exc:
        return None, (
            "Matplotlib is unavailable in the local Python environment. "
            "Text/data code can still run, but chart rendering needs a working matplotlib install. "
            f"Original import error: {exc}"
        )


def load_optional_alias(module_name: str, package_name: Optional[str] = None) -> Any:
    try:
        return __import__(module_name, fromlist=["*"])
    except Exception:
        if package_name:
            try:
                __import__(package_name)
                return __import__(module_name, fromlist=["*"])
            except Exception:
                return None
        return None


def build_thread_upload_context(thread: Dict[str, Any], exclude_asset_ids: Optional[set[str]] = None) -> str:
    uploaded_files = thread.get("uploaded_files", [])
    if not uploaded_files:
        return ""

    exclude_asset_ids = exclude_asset_ids or set()
    lines: List[str] = []
    for item in reversed(uploaded_files[-MAX_THREAD_FILE_CONTEXT_ITEMS:]):
        asset_id = item.get("asset_id", "")
        if asset_id in exclude_asset_ids:
            continue
        line_parts = [
            f"- {item.get('name', 'unnamed')}",
            f"type={item.get('content_type', 'unknown')}",
            f"size={format_bytes(int(item.get('size_bytes', 0) or 0))}",
        ]
        excerpt = trim_text(item.get("text_excerpt", ""), 900)
        line = ", ".join(line_parts)
        if excerpt:
            line += f"\n  excerpt: {excerpt}"
        lines.append(line)

    context = "\n".join(lines)
    return trim_text(context, THREAD_ATTACHMENT_CONTEXT_LIMIT)


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_attachment_prompt_block(file_meta: Dict[str, Any], extracted_text: str) -> str:
    lines = [
        f"File: {file_meta.get('name', 'unnamed')}",
        f"Type: {file_meta.get('content_type', 'unknown')}",
        f"Size: {format_bytes(int(file_meta.get('size_bytes', 0) or 0))}",
    ]
    excerpt = trim_text(extracted_text, ATTACHMENT_PROMPT_TEXT_LIMIT)
    if excerpt:
        lines.append("Extracted content:")
        lines.append(excerpt)
    else:
        lines.append("Extracted content: not available; rely on metadata or multimodal file input if attached.")
    return "\n".join(lines)


def build_attachment_part(path: Path, data: bytes, mime_type: str) -> Optional[Any]:
    if not should_send_as_multimodal_part(path, mime_type, len(data)):
        return None
    try:
        return genai.types.Part.from_bytes(data=data, mime_type=mime_type)
    except Exception:
        return None


def process_uploaded_files(
    thread: Dict[str, Any],
    upload_paths: Sequence[str],
    mongo_uri_template: str,
    mongo_password: str,
    mongo_db_name: str,
) -> Tuple[List[Dict[str, Any]], List[str], List[Any], str]:
    saved_attachments: List[Dict[str, Any]] = []
    prompt_blocks: List[str] = []
    attachment_parts: List[Any] = []
    error_message = ""

    for raw_path in upload_paths:
        path = Path(str(raw_path))
        if not path.exists():
            error_message = merge_errors(error_message, f"Uploaded file is missing: {path.name}")
            continue

        data = path.read_bytes()
        mime_type = guess_mime_type(path)
        extracted_text = extract_text_from_upload(str(path))
        asset = store.save_media(
            thread_id=thread.get("thread_id", ""),
            filename=path.name,
            data=data,
            content_type=mime_type,
            kind="file",
            source="upload",
            metadata={
                "original_name": path.name,
                "extension": path.suffix.lower(),
                "text_excerpt": trim_text(extracted_text, ATTACHMENT_STORED_TEXT_LIMIT),
            },
            mongo_uri_template=mongo_uri_template,
            mongo_password=mongo_password,
            mongo_db_name=mongo_db_name,
        )

        if asset is None:
            error_message = merge_errors(
                error_message,
                format_store_error(),
                "The file was usable for the current response, but it could not be persisted to MongoDB or local cache.",
            )
            file_meta = {
                "asset_id": uuid4().hex,
                "name": path.name,
                "content_type": mime_type,
                "size_bytes": len(data),
                "text_excerpt": trim_text(extracted_text, ATTACHMENT_STORED_TEXT_LIMIT),
                "storage_backend": "not-persisted",
            }
        else:
            file_meta = asset
            saved_attachments.append(file_meta)

        prompt_blocks.append(build_attachment_prompt_block(file_meta, extracted_text))
        part = build_attachment_part(path, data, mime_type)
        if part is not None:
            attachment_parts.append(part)

    return saved_attachments, prompt_blocks, attachment_parts, error_message


def render_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rendered: List[Dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        rendered.append({"role": role, "content": str(item.get("content", ""))})
    return rendered


def make_thread_id() -> str:
    return f"thread-{uuid4().hex}"


def build_thread(title: str, order_index: int) -> Dict[str, Any]:
    timestamp = now_ts()
    return {
        "thread_id": "",
        "title": title,
        "messages": [],
        "code": "",
        "code_status": "",
        "exec_output": "",
        "exec_image_temp_path": None,
        "uploaded_files": [],
        "image_history": [],
        "last_exec_image_asset_id": "",
        "error": "",
        "created_at": timestamp,
        "updated_at": timestamp,
        "order_index": order_index,
    }


def normalize_thread(raw_thread: Dict[str, Any], fallback_order_index: int) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    for item in raw_thread.get("messages", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        message: Dict[str, Any] = {"role": role, "content": str(item.get("content", ""))}
        if "model_content" in item:
            message["model_content"] = str(item.get("model_content", ""))
        messages.append(message)

    created_at = str(raw_thread.get("created_at") or now_ts())
    updated_at = str(raw_thread.get("updated_at") or created_at)
    try:
        order_index = int(raw_thread.get("order_index", fallback_order_index))
    except (TypeError, ValueError):
        order_index = fallback_order_index

    return {
        "thread_id": str(raw_thread.get("thread_id") or ""),
        "title": str(raw_thread.get("title") or f"Chat {fallback_order_index + 1}"),
        "messages": messages,
        "code": str(raw_thread.get("code", "")),
        "code_status": str(raw_thread.get("code_status", "")),
        "exec_output": str(raw_thread.get("exec_output", "")),
        "exec_image_temp_path": None,
        "uploaded_files": list(raw_thread.get("uploaded_files", [])),
        "image_history": list(raw_thread.get("image_history", [])),
        "last_exec_image_asset_id": str(raw_thread.get("last_exec_image_asset_id", "")),
        "error": str(raw_thread.get("error", "")),
        "created_at": created_at,
        "updated_at": updated_at,
        "order_index": order_index,
    }


def init_state() -> Dict[str, Any]:
    thread_id = make_thread_id()
    thread = build_thread("Chat 1", 0)
    thread["thread_id"] = thread_id
    return {"threads": {thread_id: thread}, "order": [thread_id], "active_id": thread_id}


def load_state() -> Dict[str, Any]:
    thread_docs = store.load_threads()
    if not thread_docs:
        state = init_state()
        if store.last_error:
            state["threads"][state["active_id"]]["error"] = format_store_error()
        return state

    normalized_threads: List[Tuple[str, Dict[str, Any]]] = []
    for idx, (thread_id, raw_thread) in enumerate(thread_docs):
        thread = normalize_thread(raw_thread, idx)
        thread["thread_id"] = thread_id
        normalized_threads.append((thread_id, thread))

    normalized_threads.sort(
        key=lambda item: (item[1].get("order_index", 0), item[1].get("created_at", ""), item[0])
    )
    threads = {thread_id: thread for thread_id, thread in normalized_threads}
    order = [thread_id for thread_id, _ in normalized_threads]
    active_id = max(
        normalized_threads,
        key=lambda item: (item[1].get("updated_at", ""), item[1].get("order_index", 0), item[0]),
    )[0]
    return {"threads": threads, "order": order, "active_id": active_id}


def persist_thread(state: Dict[str, Any], thread_id: str, touch: bool = False) -> None:
    thread = state["threads"].get(thread_id)
    if not thread:
        return
    if touch:
        thread["updated_at"] = now_ts()
    store.save_thread(thread_id, thread)
    if store.last_error:
        thread["error"] = merge_errors(thread.get("error", ""), format_store_error())


def short_thread_id(thread_id: str) -> str:
    return thread_id.replace("thread-", "")[:8]


def list_threads(state: Dict[str, Any]) -> List[Tuple[str, str]]:
    choices: List[Tuple[str, str]] = []
    for thread_id in state["order"]:
        thread = state["threads"].get(thread_id)
        if not thread:
            continue
        label = f"{thread.get('title', 'Untitled')} ({short_thread_id(thread_id)})"
        choices.append((label, thread_id))
    return choices


def refresh_thread_list(state: Dict[str, Any]) -> gr.Dropdown:
    choices = list_threads(state)
    active_id = state.get("active_id")
    if active_id not in state.get("threads", {}) and choices:
        active_id = choices[0][1]
    return gr.update(choices=choices, value=active_id)


def resolve_thread_upload(thread: Dict[str, Any], file_ref: str) -> Optional[Dict[str, Any]]:
    ref = normalize_text_block(file_ref)
    if not ref:
        return None
    for item in thread.get("uploaded_files", []):
        if ref in {item.get("asset_id", ""), item.get("name", ""), item.get("original_name", "")}:
            return item
    return None


def materialize_thread_upload(thread: Dict[str, Any], file_ref: str) -> str:
    item = resolve_thread_upload(thread, file_ref)
    if item is None:
        raise FileNotFoundError(f"No uploaded file matched '{file_ref}'.")

    filename = item.get("name") or f"{item.get('asset_id', uuid4().hex)}.bin"
    
    data = store.load_media_bytes(
        item,
        mongo_uri_template=DEFAULT_MONGODB_URI_TEMPLATE,
        mongo_password=DEFAULT_MONGODB_PASSWORD,
        mongo_db_name=DEFAULT_MONGODB_DB_NAME,
    )
    if data is None:
        raise RuntimeError(format_store_error() or f"Could not load stored bytes for '{filename}'.")
    
    # Create temporary file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix)
    temp_file.write(data)
    temp_file.close()
    return temp_file.name


def load_thread_file_value(thread: Dict[str, Any], file_ref: str) -> Any:
    path = Path(materialize_thread_upload(thread, file_ref))
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".json"}:
        return pd.read_json(path)
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(path)
    if suffix in TEXT_FILE_EXTENSIONS:
        return read_text_file(path)
    return str(path)


def load_asset_image(asset: Dict[str, Any]) -> Optional[Image.Image]:
    data = store.load_media_bytes(
        asset,
        mongo_uri_template=DEFAULT_MONGODB_URI_TEMPLATE,
        mongo_password=DEFAULT_MONGODB_PASSWORD,
        mongo_db_name=DEFAULT_MONGODB_DB_NAME,
    )
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception:
        return None


def build_image_history_gallery(thread: Dict[str, Any]) -> List[Tuple[Any, str]]:
    gallery: List[Tuple[Any, str]] = []
    for asset in thread.get("image_history", []):
        image = load_asset_image(asset)
        if image is None:
            continue
        caption = asset.get("name", "chart.png")
        gallery.append((image, caption))
    return gallery


def get_current_exec_image(thread: Dict[str, Any]) -> Optional[Any]:
    asset_id = thread.get("last_exec_image_asset_id", "")
    if asset_id:
        for asset in reversed(thread.get("image_history", [])):
            if asset.get("asset_id") == asset_id:
                image = load_asset_image(asset)
                if image is not None:
                    return image
                break
    temp_path = thread.get("exec_image_temp_path")
    if temp_path and Path(temp_path).exists():
        return temp_path
    return None


def build_uploaded_files_gallery(thread: Dict[str, Any]) -> List[Tuple[Any, str]]:
    """Build file list gallery with download support."""
    gallery: List[Tuple[Any, str]] = []
    files = thread.get("uploaded_files", [])
    for item in reversed(files):
        name = item.get("name", "unnamed")
        size_text = format_bytes(int(item.get("size_bytes", 0) or 0))
        content_type = item.get("content_type", "unknown")
        label = f"{name} ({size_text}) - {content_type}"
        gallery.append((name, label))
    return gallery


def build_file_download_choices(thread: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Build choices for file download dropdown."""
    choices: List[Tuple[str, str]] = []
    files = thread.get("uploaded_files", [])
    for item in reversed(files):
        name = item.get("name", "unnamed")
        size_text = format_bytes(int(item.get("size_bytes", 0) or 0))
        asset_id = item.get("asset_id", "")
        value = asset_id or name
        label = f"{name} ({size_text})"
        choices.append((label, value))
    return choices


def download_file(
    state: Dict[str, Any],
    file_ref: str,
) -> Optional[str]:
    """Download selected file and return path for download."""
    if not file_ref or not state.get("active_id"):
        return None
    
    thread = state["threads"].get(state["active_id"])
    if not thread:
        return None
    
    try:
        file_path = materialize_thread_upload(thread, file_ref)
        return file_path
    except Exception as e:
        print(f"Error downloading file: {e}")
        return None


def render_uploaded_files_html(thread: Dict[str, Any]) -> str:
    files = thread.get("uploaded_files", [])
    if not files:
        return (
            "<div class='sidebar-card'><div class='file-history'>"
            "<div class='file-chip'><div class='file-chip-info'><strong>Chưa có file</strong></div></div>"
            "</div></div>"
        )

    cards: List[str] = []
    for item in reversed(files):
        name = item.get("name", "unnamed")
        size_text = format_bytes(int(item.get("size_bytes", 0) or 0))
        asset_id = item.get("asset_id", "")
        file_ref = asset_id or name
        
        cards.append(
            "<div class='file-chip' data-file-ref='" + html.escape(file_ref) + "' data-file-name='" + html.escape(name) + "'>"
            "<div class='file-chip-info'>"
            f"<strong>{html.escape(name)}</strong>"
            f"<small>{html.escape(size_text)}</small>"
            "</div>"
            "<div class='file-chip-actions'>"
            f"<button class='file-action-btn download' onclick='triggerFileDownload(this)' type='button' title='Download'>⬇️</button>"
            "</div>"
            "</div>"
        )

    html_content = f"<div class='sidebar-card'><div class='file-history'>{''.join(cards)}</div></div>"
    
    # Add JavaScript handler for triggering downloads
    js_script = """
    <script>
    window.triggerFileDownload = function(btn) {
        const fileRef = btn.getAttribute('data-file-ref');
        const fileName = btn.getAttribute('data-file-name');
        if (!fileRef) return;
        
        // Create a simple link and simulate download
        // Find all inputs in the page and look for our trigger textbox
        const inputs = document.querySelectorAll('input[type="text"]');
        let triggerFound = false;
        
        for (let inp of inputs) {
            // Check if this might be our textbox by looking at nearby labels or structure
            const parent = inp.closest('[data-testid]') || inp.closest('div');
            if (parent && parent.querySelector('label') && parent.querySelector('label').textContent === '') {
                // Try to trigger change on this textbox
                inp.value = fileRef;
                inp.dispatchEvent(new Event('change', { bubbles: true }));
                inp.dispatchEvent(new Event('input', { bubbles: true }));
                triggerFound = true;
                break;
            }
        }
        
        if (!triggerFound) {
            // Fallback: look for all textboxes and try setting value
            const textboxes = document.querySelectorAll('input[type="text"]');
            if (textboxes.length > 0) {
                // Try the first hidden/invisible textbox
                for (let tb of textboxes) {
                    if (tb.offsetParent === null || tb.style.display === 'none') {
                        tb.value = fileRef;
                        tb.dispatchEvent(new Event('change', { bubbles: true }));
                        break;
                    }
                }
            }
        }
    };
    </script>
    """
    
    return html_content + js_script


# def build_download_choices(thread: Dict[str, Any]) -> List[Tuple[str, str]]:
#     choices: List[Tuple[str, str]] = []
#     files = thread.get("uploaded_files", [])
#     for item in reversed(files):
#         name = item.get("name", "unnamed")
#         size_text = format_bytes(int(item.get("size_bytes", 0) or 0))
#         asset_id = item.get("asset_id", "")
#         value = asset_id or name
#         label = f"{name} ({size_text})"
#         choices.append((label, value))
#     return choices


def get_thread_payload(
    thread: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], str, str, str, Optional[Any], List[Tuple[Any, str]], str, str, gr.Dropdown]:
    return (
        render_messages(thread.get("messages", [])),
        thread.get("code", ""),
        thread.get("code_status", ""),
        thread.get("exec_output", ""),
        get_current_exec_image(thread),
        build_image_history_gallery(thread),
        render_uploaded_files_html(thread),
        thread.get("error", ""),
        gr.update(choices=build_file_download_choices(thread), value=None),
    )


def select_thread(
    state: Dict[str, Any],
    thread_id: Optional[str],
) -> Tuple[
    Dict[str, Any],
    gr.Dropdown,
    List[Dict[str, str]],
    str,
    str,
    str,
    Optional[Any],
    List[Tuple[Any, str]],
    str,
    str,
    # gr.Dropdown,
    str,
    Any,
]:
    if not thread_id or thread_id not in state["threads"]:
        thread_id = state["active_id"]
    state["active_id"] = thread_id
    thread = state["threads"][thread_id]
    
    messages, code, code_status, exec_output, exec_image, image_gallery, file_html, error, file_download_choices = get_thread_payload(thread)
    
    return (
        state,
        refresh_thread_list(state),
        messages,
        code,
        code_status,
        exec_output,
        exec_image,
        image_gallery,
        file_html,
        error,
        "",
        gr.update(value=None),
        file_download_choices,
    )


def new_thread(
    state: Dict[str, Any],
) -> Tuple[
    Dict[str, Any],
    gr.Dropdown,
    List[Dict[str, str]],
    str,
    str,
    str,
    Optional[Any],
    List[Tuple[Any, str]],
    str,
    str,
    # gr.Dropdown,
    str,
    Any,
]:
    thread_id = make_thread_id()
    thread = build_thread(title=f"Chat {len(state['order']) + 1}", order_index=len(state["order"]))
    thread["thread_id"] = thread_id
    state["threads"][thread_id] = thread
    state["order"].append(thread_id)
    state["active_id"] = thread_id
    persist_thread(state, thread_id)
    return (
        state,
        refresh_thread_list(state),
        [],
        "",
        "",
        "",
        None,
        [],
        render_uploaded_files_html(thread),
        thread.get("error", ""),
        "",
        gr.update(value=None),
        gr.update(choices=build_file_download_choices(thread), value=None),
    )


def maybe_update_thread_title(thread: Dict[str, Any], message: str) -> None:
    title = normalize_text_block(message)
    if not title:
        return
    if thread.get("messages"):
        return
    current_title = thread.get("title", "")
    if not current_title.startswith("Chat "):
        return
    thread["title"] = trim_text(title, 48)


def build_user_text(
    message: str,
    current_upload_blocks: Sequence[str],
    thread_upload_context: str,
) -> str:
    prompt = message.strip()
    if not prompt and current_upload_blocks:
        prompt = "Hãy phân tích các file vừa tải lên và hỗ trợ tôi dựa trên chúng."

    sections = [prompt or "Please help with the uploaded material."]
    if current_upload_blocks:
        sections.append("Current uploaded files:\n" + "\n\n".join(current_upload_blocks))
    if thread_upload_context:
        sections.append("Previously uploaded files in this chat:\n" + thread_upload_context)
    return "\n\n".join(section for section in sections if section.strip())


def handle_chat(
    state: Dict[str, Any],
    config: Dict[str, Any],
    message: str,
    upload_paths: Optional[List[str]],
    mongo_uri_template: str,
    mongo_password: str,
    mongo_db_name: str,
    mongo_collection_name: str,
) -> Tuple[
    Dict[str, Any],
    gr.Dropdown,
    List[Dict[str, str]],
    str,
    str,
    str,
    Optional[Any],
    List[Tuple[Any, str]],
    str,
    str,
    # gr.Dropdown,
    str,
    Any,
]:
    thread = state["threads"][state["active_id"]]
    client = build_client()

    uploaded_paths = [str(path) for path in (upload_paths or []) if str(path).strip()]
    if not normalize_text_block(message) and not uploaded_paths:
        messages, code, code_status, exec_output, exec_image, image_gallery, file_html, error, file_download_choices = (
            get_thread_payload(thread)
        )
        return (
            state,
            refresh_thread_list(state),
            messages,
            code,
            code_status,
            exec_output,
            exec_image,
            image_gallery,
            file_html,
            error,
            "",
            gr.update(value=None),
            file_download_choices,
        )

    saved_attachments, current_upload_blocks, attachment_parts, upload_error = process_uploaded_files(
        thread=thread,
        upload_paths=uploaded_paths,
        mongo_uri_template=mongo_uri_template,
        mongo_password=mongo_password,
        mongo_db_name=mongo_db_name,
    )
    if saved_attachments:
        thread["uploaded_files"].extend(saved_attachments)

    thread_upload_context = build_thread_upload_context(
        thread,
        exclude_asset_ids={item.get("asset_id", "") for item in saved_attachments},
    )
    user_text = build_user_text(message, current_upload_blocks, thread_upload_context)
    maybe_update_thread_title(thread, message or "Files uploaded")

    knowledge_base_context, knowledge_base_status, knowledge_base_documents = lookup_knowledge_base(
        query_text=user_text,
        uri_template=mongo_uri_template,
        password=mongo_password,
        db_name=mongo_db_name,
        collection_name=mongo_collection_name,
    )
    instruction_context = build_instruction_context(user_text)
    system_prompt = render_prompt(config, knowledge_base_context, instruction_context)
    contents = build_contents(thread["messages"], user_text, attachment_parts)

    if not client:
        thread["error"] = merge_errors("Missing GEMINI_API_KEY", upload_error)
        persist_thread(state, state["active_id"], touch=True)
        messages, code, code_status, exec_output, exec_image, image_gallery, file_html, error, file_download_choices = (
            get_thread_payload(thread)
        )
        return (
            state,
            refresh_thread_list(state),
            messages,
            code,
            code_status,
            exec_output,
            exec_image,
            image_gallery,
            file_html,
            error,
            "",
            gr.update(value=None),
            file_download_choices,
        )

    try:
        response = client.models.generate_content(
            model=config.get("model", DEFAULT_MODEL),
            contents=contents,
            config={"system_instruction": system_prompt},
        )
        bot_text = response.text or ""
        error_message = ""
    except Exception as exc:
        bot_text = f"Error: {exc}"
        error_message = str(exc)

    history = thread["messages"]
    history.append({"role": "user", "content": message or "Files uploaded", "model_content": user_text})
    history.append({"role": "assistant", "content": bot_text})

    code = extract_code(bot_text) or ""
    status = "Pending approval" if code else ""
    thread["code"] = code
    thread["code_status"] = status
    thread["exec_output"] = ""
    thread["exec_image_temp_path"] = None
    thread["error"] = merge_errors(error_message, upload_error)

    store.log_event(
        {
            "type": "chat",
            "ts": now_ts(),
            "thread_id": state["active_id"],
            "thread_title": thread.get("title", ""),
            "model": config.get("model", DEFAULT_MODEL),
            "user": message,
            "user_full_text": user_text,
            "bot": bot_text,
            "assistant_code": code,
            "assistant_error": error_message,
            "upload_count": len(uploaded_paths),
            "uploaded_files": saved_attachments,
            "knowledge_base_status": knowledge_base_status,
            "knowledge_base_result_count": len(knowledge_base_documents),
            "knowledge_base_documents": knowledge_base_documents,
        }
    )

    persist_thread(state, state["active_id"], touch=True)
    thread["error"] = merge_errors(thread.get("error", ""), format_store_error())
    messages, code, code_status, exec_output, exec_image, image_gallery, file_html, error, file_download_choices = (
        get_thread_payload(thread)
    )
    return (
        state,
        refresh_thread_list(state),
        messages,
        code,
        code_status,
        exec_output,
        exec_image,
        image_gallery,
        file_html,
        error,
        "",
        gr.update(value=None),
        file_download_choices,
    )


def build_exec_globals(code: str, thread: Dict[str, Any]) -> Dict[str, Any]:
    import csv
    import datetime as dt
    import json
    import os as stdlib_os
    import random
    import re as stdlib_re
    import statistics
    import textwrap
    from collections import Counter, defaultdict
    from pathlib import Path as StdlibPath

    plot_requested = code_requests_plotting(code)
    plt, matplotlib_error = load_matplotlib_pyplot() if plot_requested else (None, "")
    if plt is None:
        plt_global: Any = MatplotlibUnavailableProxy(matplotlib_error) if plot_requested else None
    else:
        plt_global = plt

    sns = load_optional_alias("seaborn")
    px = load_optional_alias("plotly.express", "plotly")
    go = load_optional_alias("plotly.graph_objects", "plotly")

    def list_thread_files() -> List[Dict[str, Any]]:
        return [
            {
                "asset_id": item.get("asset_id", ""),
                "name": item.get("name", ""),
                "content_type": item.get("content_type", ""),
                "size_bytes": item.get("size_bytes", 0),
            }
            for item in thread.get("uploaded_files", [])
        ]

    def get_thread_file_path(file_name_or_id: str) -> str:
        return materialize_thread_upload(thread, file_name_or_id)

    def load_thread_file(file_name_or_id: str) -> Any:
        return load_thread_file_value(thread, file_name_or_id)

    return {
        "np": np,
        "pd": pd,
        "plt": plt_global,
        "sns": sns,
        "px": px,
        "go": go,
        "os": stdlib_os,
        "re": stdlib_re,
        "json": json,
        "math": math,
        "random": random,
        "statistics": statistics,
        "textwrap": textwrap,
        "csv": csv,
        "io": io,
        "datetime": dt,
        "Path": StdlibPath,
        "Counter": Counter,
        "defaultdict": defaultdict,
        "load_kb_collection": load_kb_collection,
        "get_kb_collection_schema": get_kb_collection_schema,
        "list_kb_collections": list_kb_collections,
        "list_thread_files": list_thread_files,
        "get_thread_file_path": get_thread_file_path,
        "load_thread_file": load_thread_file,
    }


def run_code(code: str, thread: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    stdout_buffer = io.StringIO()
    image_path = None
    local_env: Dict[str, Any] = {}
    old_stdout = None
    try:
        exec_globals = build_exec_globals(code, thread)
        plt = exec_globals.get("plt")

        def try_exec() -> None:
            exec(code, exec_globals, local_env)

        old_stdout = sys.stdout
        sys.stdout = stdout_buffer
        try_exec()
        sys.stdout = old_stdout

        if plt is not None and plt.get_fignums():
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            plt.savefig(tmp.name, bbox_inches="tight")
            image_path = tmp.name
            plt.close("all")
    except ModuleNotFoundError as exc:
        return f"Execution error: missing Python package '{exc.name}'. Install it in the environment and retry.", None
    except ImportError as exc:
        if "_c_internal_utils" in str(exc) or "matplotlib" in str(exc).lower():
            return (
                "Execution error: Matplotlib could not be imported in the local Python environment. "
                "Non-chart code can still run, but chart code needs a working matplotlib install. "
                f"Original error: {exc}",
                None,
            )
        return f"Execution error: {exc}", None
    except Exception as exc:
        return f"Execution error: {exc}", None
    finally:
        try:
            if old_stdout is not None:
                sys.stdout = old_stdout
        except Exception:
            pass

    output = stdout_buffer.getvalue().strip()
    return output or "(no text output)", image_path


def approve_code(
    state: Dict[str, Any],
    code: str,
    mongo_uri_template: str,
    mongo_password: str,
    mongo_db_name: str,
) -> Tuple[Dict[str, Any], str, Optional[Any], str, List[Tuple[Any, str]], str]:
    thread = state["threads"][state["active_id"]]
    output, image_path = run_code(code, thread)

    thread["exec_output"] = output
    thread["code_status"] = "Approved and executed"
    thread["exec_image_temp_path"] = image_path

    if image_path and Path(image_path).exists():
        image_bytes = Path(image_path).read_bytes()
        image_asset = store.save_media(
            thread_id=thread.get("thread_id", ""),
            filename=f"{thread.get('title', 'chat').replace(' ', '_')}_chart.png",
            data=image_bytes,
            content_type="image/png",
            kind="image",
            source="execution",
            metadata={"caption": thread.get("title", "Chart image")},
            mongo_uri_template=mongo_uri_template,
            mongo_password=mongo_password,
            mongo_db_name=mongo_db_name,
        )
        if image_asset is not None:
            thread["image_history"].append(image_asset)
            thread["last_exec_image_asset_id"] = image_asset.get("asset_id", "")
        else:
            thread["error"] = merge_errors(
                thread.get("error", ""),
                format_store_error(),
                "The chart was generated locally, but it could not be persisted to MongoDB or local cache.",
            )

    store.log_event(
        {
            "type": "code_execution",
            "ts": now_ts(),
            "thread_id": state["active_id"],
            "thread_title": thread.get("title", ""),
            "code": code,
            "output": output,
            "image_created": bool(image_path),
            "image_history_count": len(thread.get("image_history", [])),
        }
    )

    persist_thread(state, state["active_id"], touch=True)
    thread["error"] = merge_errors(thread.get("error", ""), format_store_error())
    return (
        state,
        thread["exec_output"],
        get_current_exec_image(thread),
        thread["code_status"],
        build_image_history_gallery(thread),
        thread.get("error", ""),
    )


def load_app_state() -> Tuple[
    Dict[str, Any],
    gr.Dropdown,
    List[Dict[str, str]],
    str,
    str,
    str,
    Optional[Any],
    List[Tuple[Any, str]],
    str,
    str,
    str,
    Any,
]:
    state = load_state()
    thread = state["threads"][state["active_id"]]
    messages, code, code_status, exec_output, exec_image, image_gallery, file_html, error, file_download_choices = get_thread_payload(thread)
    thread["error"] = merge_errors(error, format_store_error())
    return (
        state,
        refresh_thread_list(state),
        messages,
        code,
        code_status,
        exec_output,
        exec_image,
        image_gallery,
        file_html,
        thread["error"],
        "",
        gr.update(value=None),
        file_download_choices,
    )


def preinstall_whitelist() -> None:
    if not AUTO_INSTALL_WHITELIST:
        return
    import subprocess

    for lib in PREINSTALL_LIBS:
        try:
            __import__(lib.replace("-", "_"))
        except Exception:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", lib])
            except Exception:
                pass


store = FirebaseStore()
knowledge_base = MongoKnowledgeBase()


def debug_check_mongo() -> None:
    uri, error = build_mongodb_uri(DEFAULT_MONGODB_URI_TEMPLATE, DEFAULT_MONGODB_PASSWORD)
    if error:
        print(f"[DEBUG] MongoDB check failed: {error}")
        return

    try:
        from pymongo import MongoClient

        db_name = normalize_text_block(DEFAULT_MONGODB_DB_NAME)
        collection_names = knowledge_base._parse_collection_names(DEFAULT_MONGODB_COLLECTION_NAME)
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")

        if not db_name or not collection_names:
            print("[DEBUG] MongoDB connected, but MONGODB_DB_NAME or MONGODB_COLLECTION_NAME is blank.")
            return

        counts = []
        total = 0
        for collection_name in collection_names:
            collection = client[db_name][collection_name]
            estimated_count = collection.estimated_document_count()
            counts.append(f"{collection_name}={estimated_count}")
            total += estimated_count

        print(
            f"[DEBUG] MongoDB OK: connected to {db_name} across {len(collection_names)} collection(s): "
            f"{', '.join(counts)} (total estimated {total} document(s))."
        )
    except Exception as exc:  # pragma: no cover - debug helper
        print(f"[DEBUG] MongoDB check error: {exc}")

def build_chat_blocks() -> gr.Blocks:
    with gr.Blocks(title="Data Visualize Chatbox - Chat") as chat_demo:
        state = gr.State(init_state())
        config_state = gr.State({"behavior": "", "model": DEFAULT_MODEL})

        with gr.Row():
            with gr.Column(scale=1, min_width=280):
                thread_list = gr.Dropdown(choices=[], label="Conversations")
                new_chat_btn = gr.Button("New chat")

                gr.Markdown("### Chart history")
                image_history = gr.Gallery(
                    label="Generated images",
                    columns=2,
                    height=250,
                    object_fit="contain",
                    elem_classes=["image-history-grid"],
                )

                gr.Markdown("### Uploaded files")
                file_history = gr.HTML(render_uploaded_files_html(build_thread("Chat 1", 0)))

                file_download_trigger = gr.Textbox(visible=False, interactive=False)
                file_download_select = gr.Dropdown(
                    label="Select file to download",
                    choices=[],
                    interactive=True,
                    visible=False,
                )
                file_download_btn = gr.Button("📥 Download", visible=False, scale=1)

                file_download_output = gr.File(label="Downloaded file", interactive=False)

                mongo_uri_template = gr.Textbox(value=DEFAULT_MONGODB_URI_TEMPLATE, visible=False)
                mongo_password = gr.Textbox(value=DEFAULT_MONGODB_PASSWORD, visible=False)
                mongo_db_name = gr.Textbox(value=DEFAULT_MONGODB_DB_NAME, visible=False)
                mongo_collection_name = gr.Textbox(value=DEFAULT_MONGODB_COLLECTION_NAME, visible=False)

            with gr.Column(scale=4):
                chatbot = gr.Chatbot(label="Chat")
                with gr.Row():
                    message = gr.Textbox(
                        label="Message",
                        scale=4,
                        placeholder="Đặt câu hỏi về dữ liệu, file đã upload hoặc yêu cầu vẽ biểu đồ...",
                    )
                    send_btn = gr.Button("Send", scale=1)
                upload_ctx = gr.File(label="Attach files", file_count="multiple", type="filepath")
                code_box = gr.Code(label="Generated code", language="python")
                code_status = gr.Textbox(label="Code status", interactive=False)
                approve_btn = gr.Button("Approve and run")
                exec_output = gr.Textbox(label="Execution output", interactive=False)
                exec_image = gr.Image(label="Latest chart / execution image")
                error_box = gr.Textbox(label="Error", interactive=False, lines=4)

        load_outputs = [
            state,
            thread_list,
            chatbot,
            code_box,
            code_status,
            exec_output,
            exec_image,
            image_history,
            file_history,
            error_box,
            message,
            upload_ctx,
            file_download_select,
        ]
        chat_demo.load(fn=load_app_state, outputs=load_outputs)

        thread_list.change(fn=select_thread, inputs=[state, thread_list], outputs=load_outputs)
        new_chat_btn.click(fn=new_thread, inputs=[state], outputs=load_outputs)

        chat_inputs = [
            state,
            config_state,
            message,
            upload_ctx,
            mongo_uri_template,
            mongo_password,
            mongo_db_name,
            mongo_collection_name,
        ]
        send_btn.click(fn=handle_chat, inputs=chat_inputs, outputs=load_outputs)
        message.submit(fn=handle_chat, inputs=chat_inputs, outputs=load_outputs)

        file_download_btn.click(
            fn=download_file,
            inputs=[state, file_download_select],
            outputs=file_download_output,
        )

        approve_btn.click(
            fn=approve_code,
            inputs=[state, code_box, mongo_uri_template, mongo_password, mongo_db_name],
            outputs=[state, exec_output, exec_image, code_status, image_history, error_box],
        )

        file_download_trigger.change(
            fn=lambda file_ref, state_val: download_file(state_val, file_ref) if file_ref else None,
            inputs=[file_download_trigger, state],
            outputs=file_download_output,
        )

    return chat_demo


def build_model_blocks() -> gr.Blocks:
    with gr.Blocks(title="Data Visualize Chatbox - Model") as model_demo:
        build_property_model_page()
    return model_demo


if __name__ == "__main__":
    preinstall_whitelist()
    try:
        knowledge_base._connect(
            DEFAULT_MONGODB_URI_TEMPLATE,
            DEFAULT_MONGODB_PASSWORD,
            DEFAULT_MONGODB_DB_NAME,
            DEFAULT_MONGODB_COLLECTION_NAME,
        )
    except Exception as exc:
        if KB_ENABLE_TIMING_LOGS:
            print(f"[KB INIT] preload/connect error: {exc}")
    debug_check_mongo()
    chat_demo = build_chat_blocks()
    model_demo = build_model_blocks()
    demo = gr.TabbedInterface([chat_demo, model_demo], ["Chat", "Model"], title="Data Visualize Chatbox")
    demo.launch(css=APP_CSS)
