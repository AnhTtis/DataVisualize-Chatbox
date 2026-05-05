# Gradio Demo Bot

This is a Gradio demo with:
- Chat UI with Gemini and conversation list
- Knowledge Base configuration and Google Drive uploads
- Model page (placeholder)
- Human approval before executing AI-generated code
- Firebase storage for chat history, code, and outputs

## Setup

1. Create a Python environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill values.
4. Start the app:
   ```bash
   python app.py
   ```
   Or:
   ```bash
   python -m app
   ```
   Do not use `python -m app.py`; Python treats `app.py` as a module path there and it fails.

## Notes

- Google Drive upload uses a service account JSON in `GDRIVE_SERVICE_ACCOUNT_JSON`.
- Firebase storage uses a service account JSON in `FIREBASE_CREDENTIALS_JSON`.
- Code execution is a demo and should be sandboxed for production.
- Knowledge Base can be attached per conversation.
