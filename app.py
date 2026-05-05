import io
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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


def now_ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class FirebaseStore:
    def __init__(self) -> None:
        self.project_id = os.getenv("FIREBASE_PROJECT_ID")
        self.credentials_path = os.getenv("FIREBASE_CREDENTIALS_JSON")
        self.enabled = bool(self.project_id and self.credentials_path)
        self._init_client()

    def _init_client(self) -> None:
        if not self.enabled:
            self.client = None
            return
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore

            if not firebase_admin._apps:
                cred = credentials.Certificate(self.credentials_path)
                firebase_admin.initialize_app(cred, {"projectId": self.project_id})
            self.client = firestore.client()
        except Exception:
            self.client = None

    def log_event(self, event: Dict[str, Any]) -> None:
        if not self.client:
            return
        try:
            self.client.collection("events").add(event)
        except Exception:
            pass


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


def render_prompt(config: Dict[str, Any]) -> str:
    parts = ["You are a helpful assistant."]
    behavior = config.get("behavior", "")
    if behavior:
        parts.append(f"Behavior: {behavior}")
    parts.append(
        "If you generate code, wrap it in a fenced block using triple backticks."
    )
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
        content = msg.get("content", "")
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


def init_state() -> Dict[str, Any]:
    thread_id = f"thread-{utc_timestamp()}"
    return {
        "threads": {
            thread_id: {
                "title": "New chat",
                "messages": [],
                "code": "",
                "code_status": "",
                "exec_output": "",
                "exec_image": None,
                "error": "",
            }
        },
        "order": [thread_id],
        "active_id": thread_id,
    }


def get_thread_payload(thread: Dict[str, Any]) -> Tuple[List[Dict[str, str]], str, str, str, Optional[str], str]:
    return (
        thread.get("messages", []),
        thread.get("code", ""),
        thread.get("code_status", ""),
        thread.get("exec_output", ""),
        thread.get("exec_image", None),
        thread.get("error", ""),
    )


def list_threads(state: Dict[str, Any]) -> List[str]:
    return [state["threads"][tid]["title"] for tid in state["order"]]


def refresh_thread_list(state: Dict[str, Any]) -> gr.Dropdown:
    choices = list_threads(state)
    active_title = state["threads"][state["active_id"]]["title"]
    return gr.update(choices=choices, value=active_title)


def select_thread(
    state: Dict[str, Any],
    label: Optional[str],
) -> Tuple[Dict[str, Any], List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    if not label:
        thread = state["threads"][state["active_id"]]
        messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
        return state, messages, code, code_status, exec_output, exec_image, error, ""
    for tid in state["order"]:
        if state["threads"][tid]["title"] == label:
            state["active_id"] = tid
            break
    thread = state["threads"][state["active_id"]]
    messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
    return state, messages, code, code_status, exec_output, exec_image, error, ""


def new_thread(
    state: Dict[str, Any],
    config: Dict[str, Any],
) -> Tuple[Dict[str, Any], gr.Dropdown, List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    thread_id = f"thread-{utc_timestamp()}"
    state["threads"][thread_id] = {
        "title": f"Chat {len(state['order']) + 1}",
        "messages": [],
        "code": "",
        "code_status": "",
        "exec_output": "",
        "exec_image": None,
        "error": "",
    }
    state["order"].append(thread_id)
    state["active_id"] = thread_id
    return state, refresh_thread_list(state), [], "", "", "", None, "", ""


def handle_chat(
    state: Dict[str, Any],
    config: Dict[str, Any],
    message: str,
    upload: Optional[List[gr.File]],
) -> Tuple[Dict[str, Any], List[Dict[str, str]], str, str, str, Optional[str], str, str]:
    client = build_client()
    system_prompt = render_prompt(config)

    extra_text = ""
    if upload:
        for f in upload:
            extra_text += get_text_from_upload(f.name)

    user_text = message
    if extra_text:
        user_text += "\n\nAttached content:\n" + extra_text

    thread = state["threads"][state["active_id"]]
    history = thread["messages"]
    contents = build_contents(history, user_text)

    if not client:
        thread["error"] = "Missing GEMINI_API_KEY"
        messages, code, code_status, exec_output, exec_image, error = get_thread_payload(thread)
        return state, messages, code, code_status, exec_output, exec_image, error, ""

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

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": bot_text})

    store.log_event({
        "type": "chat",
        "ts": now_ts(),
        "thread_id": state["active_id"],
        "user": message,
        "bot": bot_text,
        "prompt": system_prompt,
    })

    code = extract_code(bot_text) or ""
    status = "Pending approval" if code else ""
    thread["code"] = code
    thread["code_status"] = status
    thread["exec_output"] = ""
    thread["exec_image"] = None
    thread["error"] = error_message
    return state, history, code, status, "", None, error_message, ""


def approve_code(state: Dict[str, Any], code: str) -> Tuple[Dict[str, Any], str, Optional[str], str]:
    output, image_path = run_code(code)
    thread = state["threads"][state["active_id"]]
    thread["exec_output"] = output
    thread["exec_image"] = image_path
    thread["code_status"] = "Approved and executed"
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


with gr.Blocks(title="Gradio Demo Bot") as demo:
    state = gr.State(init_state())
    config_state = gr.State({"behavior": "", "model": DEFAULT_MODEL})

    with gr.Row():
        with gr.Column(scale=1, min_width=220):
            nav = gr.Radio(choices=["Chat", "Model"], value="Chat", label="Navigator")
            thread_list = gr.Dropdown(choices=[], label="Conversations")
            new_chat_btn = gr.Button("New chat")

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

    demo.load(fn=refresh_thread_list, inputs=[state], outputs=[thread_list])
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
        inputs=[state, config_state, message, upload_ctx],
        outputs=[state, chatbot, code_box, code_status, exec_output, exec_image, error_box, message],
    )

    approve_btn.click(
        fn=approve_code,
        inputs=[state, code_box],
        outputs=[state, exec_output, exec_image, code_status],
    )

if __name__ == "__main__":
    preinstall_whitelist()
    demo.launch()
