import io
import os
import re
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from uuid import uuid4

import gradio as gr

import app_model
from dotenv import load_dotenv
from google import genai

load_dotenv()

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AUTO_INSTALL_WHITELIST = os.getenv("AUTO_INSTALL_WHITELIST", "1") == "0"
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

ALLOWED_UPLOAD_EXTS = [".txt", ".pdf", ".csv", ".doc", ".docx", ".png", ".jpg", ".jpeg"]
DEFAULT_MONGODB_URI_TEMPLATE = os.getenv(
    "MONGODB_URI_TEMPLATE",
    "mongodb+srv://visualizer:<db_password>@propertyanalysis.clzm37k.mongodb.net/",
)
DEFAULT_MONGODB_PASSWORD = os.getenv("MONGODB_PASSWORD", "")
DEFAULT_MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "")
DEFAULT_MONGODB_COLLECTION_NAME = os.getenv("MONGODB_COLLECTION_NAME", "")
# Import stores and shared KB constants/helpers
# Import KB flag for including full documents in prompt
from stores import (
    FirebaseStore,
    MongoKnowledgeBase,
    SEARCH_TOKEN_PATTERN,
    KB_ENABLE_TIMING_LOGS,
    KB_INCLUDE_ALL_DOCS_IN_PROMPT,
)
# KB tuning values are read from environment by `stores.py`


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_text_block(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokenize_search_text(text: str) -> List[str]:
    tokens = [token.lower() for token in SEARCH_TOKEN_PATTERN.findall(text)]
    return [token for token in tokens if len(token) > 1]


def log_kb_timing(operation: str, duration_ms: float, details: str = "") -> None:
    """Log KB operation timing if enabled."""
    if KB_ENABLE_TIMING_LOGS:
        suffix = f" ({details})" if details else ""
        print(f"[KB TIMING] {operation}: {duration_ms:.1f}ms{suffix}")


def build_mongodb_uri(uri_template: str, password: str) -> Tuple[Optional[str], str]:
    template = (uri_template or "").strip()
    if not template:
        return None, "Missing MongoDB URI template."
    if "<db_password>" in template:
        if not password:
            return None, "MongoDB password is blank. Fill it in before using the Knowledge Base."
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



def format_store_error() -> str:
    if not store.last_error:
        return ""
    last_line = store.last_error.strip().splitlines()[-1].strip()
    if "does not exist for project" in store.last_error and "database" in store.last_error.lower():
        return (
            f"Firebase error: {last_line}. Create a Firestore database for project "
            f"'{store.project_id}' or set FIREBASE_DATABASE_ID to an existing database."
        )
    return f"Firebase error: {last_line}"


def merge_errors(*messages: str) -> str:
    cleaned: List[str] = []
    seen: set[str] = set()
    for message in messages:
        text = (message or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return "\n\n".join(cleaned)


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
) -> Tuple[str, str]:
    # If configured to include all documents, return full preloaded/scanned docs.
    if KB_INCLUDE_ALL_DOCS_IN_PROMPT:
        # Ensure connection and preload have run
        collections = knowledge_base._connect(
            uri_template, password, db_name, collection_name
        )
        if collections is None:
            return "", knowledge_base.last_error
        documents = knowledge_base.get_all_documents_for_prompt()
        return format_knowledge_base_context(documents), (
            f"Knowledge Base included all documents from {len(collections)} collection(s)."
        )

    documents, status = knowledge_base.search(
        query_text=query_text,
        uri_template=uri_template,
        password=password,
        db_name=db_name,
        collection_names=collection_name,
    )
    return format_knowledge_base_context(documents), status


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
    return "\n\n".join(m.strip() for m in matches if m.strip())


def render_prompt(config: Dict[str, Any], knowledge_base_context: str = "") -> str:
    parts = ["You are a helpful assistant."]
    behavior = config.get("behavior", "")
    if behavior:
        parts.append(f"Behavior: {behavior}")
    parts.append(
        "If relevant Knowledge Base context is provided, use it first and say when you are relying on it."
    )
    parts.append(
        "If the Knowledge Base is unavailable, irrelevant, or incomplete, still answer helpfully using your own reasoning."
    )
    parts.append("Do not refuse general questions just because they are outside the Knowledge Base.")
    parts.append(
        "If you generate code, wrap it in a fenced block using triple backticks."
    )
    if knowledge_base_context:
        parts.append("Knowledge Base Context:")
        parts.append(knowledge_base_context)
    return "\n".join(parts)


def build_contents(
    messages: List[Dict[str, str]],
    user_text: str,
    max_messages: int = 12,
) -> List[Dict[str, Any]]:
    contents: List[Dict[str, Any]] = []
    start = max(0, len(messages) - max_messages)
    for msg in messages[start:]:
        role = msg.get("role", "user")
        content = msg.get("model_content", msg.get("content", ""))
        api_role = "user" if role == "user" else "model"
        contents.append({"role": api_role, "parts": [{"text": content}]})
    contents.append({"role": "user", "parts": [{"text": user_text}]})
    return contents


def get_text_from_upload(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt" or ext == ".csv":
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""
    return ""


def run_code(code: str) -> Tuple[str, Optional[str]]:
    stdout_buffer = io.StringIO()
    image_path = None
    local_env: Dict[str, Any] = {}
    # Demo-only execution. Use a sandbox for production.
    try:
        import sys
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        def try_exec() -> None:
            exec(code, {"plt": plt}, local_env)

        def install_module(module_name: str) -> None:
            # Best-effort install for missing modules in generated code.
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", module_name])

        old_stdout = sys.stdout
        sys.stdout = stdout_buffer
        try:
            try_exec()
        except ModuleNotFoundError as exc:
            missing = exc.name
            if not missing:
                raise
            install_module(missing)
            try_exec()
        sys.stdout = old_stdout

        if plt.get_fignums():
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            plt.savefig(tmp.name)
            image_path = tmp.name
            plt.close("all")
    except Exception as exc:
        return f"Execution error: {exc}", None
    finally:
        try:
            sys.stdout = old_stdout
        except Exception:
            pass
    output = stdout_buffer.getvalue().strip()
    return output or "(no text output)", image_path


def preinstall_whitelist() -> None:
    if not AUTO_INSTALL_WHITELIST:
        return
    import sys
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


# Quick debug helper: check MongoDB connectivity using current env vars.
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



def make_thread_id() -> str:
    return f"thread-{uuid4().hex}"


def build_thread(title: str, order_index: int) -> Dict[str, Any]:
    timestamp = now_ts()
    return {
        "title": title,
        "messages": [],
        "code": "",
        "code_status": "",
        "exec_output": "",
        "exec_image": None,
        "error": "",
        "created_at": timestamp,
        "updated_at": timestamp,
        "order_index": order_index,
    }


def render_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    rendered: List[Dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        rendered.append({
            "role": role,
            "content": str(item.get("content", "")),
        })
    return rendered


def normalize_thread(raw_thread: Dict[str, Any], fallback_order_index: int) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    for item in raw_thread.get("messages", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        message: Dict[str, Any] = {
            "role": role,
            "content": str(item.get("content", "")),
        }
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
        "title": str(raw_thread.get("title") or f"Chat {fallback_order_index + 1}"),
        "messages": messages,
        "code": str(raw_thread.get("code", "")),
        "code_status": str(raw_thread.get("code_status", "")),
        "exec_output": str(raw_thread.get("exec_output", "")),
        "exec_image": None,
        "error": str(raw_thread.get("error", "")),
        "created_at": created_at,
        "updated_at": updated_at,
        "order_index": order_index,
    }


def init_state() -> Dict[str, Any]:
    thread_id = make_thread_id()
    return {
        "threads": {thread_id: build_thread("Chat 1", 0)},
        "order": [thread_id],
        "active_id": thread_id,
    }


def load_state() -> Dict[str, Any]:
    thread_docs = store.load_threads()
    if not thread_docs:
        state = init_state()
        if store.last_error:
            state["threads"][state["active_id"]]["error"] = format_store_error()
        return state

    normalized_threads: List[Tuple[str, Dict[str, Any]]] = []
    for idx, (thread_id, raw_thread) in enumerate(thread_docs):
        normalized_threads.append((thread_id, normalize_thread(raw_thread, idx)))

    normalized_threads.sort(
        key=lambda item: (
            item[1].get("order_index", 0),
            item[1].get("created_at", ""),
            item[0],
        )
    )

    threads = {thread_id: thread for thread_id, thread in normalized_threads}
    order = [thread_id for thread_id, _ in normalized_threads]
    active_id = max(
        normalized_threads,
        key=lambda item: (
            item[1].get("updated_at", ""),
            item[1].get("order_index", 0),
            item[0],
        ),
    )[0]
    return {
        "threads": threads,
        "order": order,
        "active_id": active_id,
    }


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


def get_thread_payload(thread: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str, str, str, Optional[str], str]:
    return (
        render_messages(thread.get("messages", [])),
        thread.get("code", ""),
        thread.get("code_status", ""),
        thread.get("exec_output", ""),
        thread.get("exec_image", None),
        thread.get("error", ""),
    )


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


def select_thread(
    state: Dict[str, Any],
    thread_id: Optional[str],
) -> Tuple[Dict[str, Any], List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    if not thread_id or thread_id not in state["threads"]:
        thread = state["threads"][state["active_id"]]
        messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
        return state, messages, code, code_status, exec_output, exec_image, error, ""
    state["active_id"] = thread_id
    thread = state["threads"][thread_id]
    messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
    return state, messages, code, code_status, exec_output, exec_image, error, ""


def new_thread(
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], gr.Dropdown, List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    thread_id = make_thread_id()
    state["threads"][thread_id] = build_thread(
        title=f"Chat {len(state['order']) + 1}",
        order_index=len(state["order"]),
    )
    state["order"].append(thread_id)
    state["active_id"] = thread_id
    persist_thread(state, thread_id)
    thread = state["threads"][thread_id]
    return state, refresh_thread_list(state), [], "", "", "", None, thread.get("error", ""), ""


def handle_chat(
    state: Dict[str, Any],
    config: Dict[str, Any],
    message: str,
    upload: Optional[List[gr.File]],
    mongo_uri_template: str,
    mongo_password: str,
    mongo_db_name: str,
    mongo_collection_name: str,
) -> Tuple[Dict[str, Any], List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    client = build_client()

    extra_text = ""
    if upload:
        for f in upload:
            extra_text += get_text_from_upload(f.name)

    user_text = message
    if extra_text:
        user_text += "\n\nAttached content:\n" + extra_text

    knowledge_base_context, knowledge_base_status = lookup_knowledge_base(
        query_text=user_text,
        uri_template=mongo_uri_template,
        password=mongo_password,
        db_name=mongo_db_name,
        collection_name=mongo_collection_name,
    )
    system_prompt = render_prompt(config, knowledge_base_context)
    current_turn_text = user_text
    if knowledge_base_context:
        current_turn_text += "\n\nRelevant Knowledge Base Context:\n" + knowledge_base_context

    thread = state["threads"][state["active_id"]]
    history = thread["messages"]
    contents = build_contents(history, current_turn_text)

    if not client:
        thread["error"] = "Missing GEMINI_API_KEY"
        messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
        return (
            state,
            messages,
            code,
            code_status,
            exec_output,
            exec_image,
            error,
            "",
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

    history.append({"role": "user", "content": message, "model_content": user_text})
    history.append({"role": "assistant", "content": bot_text})

    store.log_event({
        "type": "chat",
        "ts": now_ts(),
        "thread_id": state["active_id"],
        "user": message,
        "bot": bot_text,
        "prompt": system_prompt,
        "knowledge_base_status": knowledge_base_status,
        "knowledge_base_context": knowledge_base_context,
    })

    code = extract_code(bot_text) or ""
    status = "Pending approval" if code else ""
    thread["code"] = code
    thread["code_status"] = status
    thread["exec_output"] = ""
    thread["exec_image"] = None
    thread["error"] = error_message
    persist_thread(state, state["active_id"], touch=True)
    final_error = merge_errors(thread.get("error", ""), format_store_error())
    thread["error"] = final_error
    return (
        state,
        render_messages(history),
        code,
        status,
        "",
        None,
        final_error,
        "",
    )


def approve_code(state: Dict[str, Any], code: str) -> Tuple[Dict[str, Any], str, Optional[str], str]:
    output, image_path = run_code(code)
    thread = state["threads"][state["active_id"]]
    thread["exec_output"] = output
    thread["exec_image"] = image_path
    thread["code_status"] = "Approved and executed"
    persist_thread(state, state["active_id"], touch=True)
    store.log_event({
        "type": "code_execution",
        "ts": now_ts(),
        "code": code,
        "output": output,
        "image": image_path,
    })
    return state, output, image_path, "Approved and executed"


def set_page(page: str) -> Tuple[str, str]:
    return (
        gr.update(visible=(page == "Chat")),
        gr.update(visible=(page == "Model")),
    )


def load_app_state() -> Tuple[Dict[str, Any], gr.Dropdown, List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    state = load_state()
    thread = state["threads"][state["active_id"]]
    messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
    error = merge_errors(error, format_store_error())
    thread["error"] = error
    return state, refresh_thread_list(state), messages, code, code_status, exec_output, exec_image, error, ""


with gr.Blocks(title="Gradio Demo Bot") as demo:
    state = gr.State(init_state())
    config_state = gr.State({"behavior": "", "model": DEFAULT_MODEL})

    with gr.Row():
        with gr.Column(scale=1, min_width=220):
            nav = gr.Radio(choices=["Chat", "Model"], value="Chat", label="Navigator")
            thread_list = gr.Dropdown(choices=[], label="Conversations")
            new_chat_btn = gr.Button("New chat")
            # Hidden Knowledge Base inputs (use env defaults). KB status/context won't be shown in the UI.
            mongo_uri_template = gr.Textbox(value=DEFAULT_MONGODB_URI_TEMPLATE, visible=False)
            mongo_password = gr.Textbox(value=DEFAULT_MONGODB_PASSWORD, visible=False)
            mongo_db_name = gr.Textbox(value=DEFAULT_MONGODB_DB_NAME, visible=False)
            mongo_collection_name = gr.Textbox(value=DEFAULT_MONGODB_COLLECTION_NAME, visible=False)

        with gr.Column(scale=4):
            with gr.Group(visible=True) as chat_page:
                chatbot = gr.Chatbot(label="Chat")
                with gr.Row():
                    message = gr.Textbox(label="Message", scale=4)
                    send_btn = gr.Button("Send", scale=1)
                upload_ctx = gr.File(label="Attach content (+)", file_count="multiple", file_types=ALLOWED_UPLOAD_EXTS)
                code_box = gr.Code(label="Generated code", language="python")
                code_status = gr.Textbox(label="Code status", interactive=False)
                approve_btn = gr.Button("Approve and run")
                exec_output = gr.Textbox(label="Execution output", interactive=False)
                exec_image = gr.Image(label="Execution image")
                error_box = gr.Textbox(label="Error", interactive=False)

            with gr.Group(visible=False) as model_page:
                app_model.build_model_page()

    nav.change(fn=set_page, inputs=[nav], outputs=[chat_page, model_page])

    demo.load(
        fn=load_app_state,
        outputs=[state, thread_list, chatbot, code_box, code_status, exec_output, exec_image, error_box, message],
    )
    thread_list.change(
        fn=select_thread,
        inputs=[state, thread_list],
        outputs=[state, chatbot, code_box, code_status, exec_output, exec_image, error_box, message],
    )
    new_chat_btn.click(
        fn=new_thread,
        inputs=[state, config_state],
        outputs=[state, thread_list, chatbot, code_box, code_status, exec_output, exec_image, error_box, message],
    )

    send_btn.click(
        fn=handle_chat,
        inputs=[
            state,
            config_state,
            message,
            upload_ctx,
            mongo_uri_template,
            mongo_password,
            mongo_db_name,
            mongo_collection_name,
        ],
        outputs=[
            state,
            chatbot,
            code_box,
            code_status,
            exec_output,
            exec_image,
            error_box,
            message,
        ],
    )

    approve_btn.click(
        fn=approve_code,
        inputs=[state, code_box],
        outputs=[state, exec_output, exec_image, code_status],
    )

if __name__ == "__main__":
    preinstall_whitelist()
    # Initialize Knowledge Base (create indexes and preload documents) if env vars are set
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
    demo.launch()
