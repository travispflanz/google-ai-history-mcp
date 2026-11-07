import json
import zipfile
from pathlib import Path

from google_ai_history.db import get_chat, list_chats, search_chats
from google_ai_history.importers import import_from_path


def test_import_takeout_multi_surface_and_zip(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    takeout_data = [
        {
            "header": "Gemini Apps",
            "title": "Prompted Explain quantum computing in simple terms",
            "time": "2026-11-01T12:00:00.000Z",
            "safeHtmlItem": [
                {"html": "<p>Quantum computing uses <b>qubits</b> instead of classical bits.</p>"}
            ],
        },
        {
            "header": "Gemini Apps",
            "title": "Prompted Give me a Python example with Qiskit",
            "time": "2026-11-01T12:05:00.000Z",
            "safeHtmlItem": [{"html": "<pre><code>from qiskit import QuantumCircuit</code></pre>"}],
        },
        {
            "header": "AI Mode",
            "title": "Asked in AI Mode Best espresso grinders under $500",
            "time": "2026-11-02T15:30:00.000Z",
            "titleUrl": "https://www.google.com/search?udm=50&q=espresso",
            "safeHtmlItem": [{"html": "<p>Top grinders include Baratza Encore ESP and DF54.</p>"}],
        },
        {
            "header": "Maps",
            "title": "Asked Maps Dog-friendly patios with heaters in Kansas City",
            "time": "2026-11-03T18:00:00.000Z",
            "safeHtmlItem": [
                {"html": "<p>Char Bar and Parlor KC have heated dog-friendly patios.</p>"}
            ],
        },
        {
            "header": "YouTube",
            "title": "Asked Summarize the key announcements in this keynote",
            "time": "2026-11-04T19:00:00.000Z",
            "safeHtmlItem": [
                {"html": "<p>The keynote introduced Gemini 3 and new MCP tooling.</p>"}
            ],
        },
    ]

    json_file = src_dir / "MyActivity.json"
    json_file.write_text(json.dumps(takeout_data), encoding="utf-8")

    zip_file = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.write(json_file, arcname="Takeout/My Activity/Gemini Apps/MyActivity.json")

    res = import_from_path(zip_file, base_dir=db_dir)
    assert res["imported_count"] == 4

    assert list_chats(source="takeout_gemini", base_dir=db_dir)["returned"] == 1
    assert list_chats(source="takeout_search_ai_mode", base_dir=db_dir)["returned"] == 1
    assert list_chats(source="takeout_ask_maps", base_dir=db_dir)["returned"] == 1
    assert list_chats(source="takeout_youtube_ai", base_dir=db_dir)["returned"] == 1

    q_search = search_chats("Qiskit qubits", base_dir=db_dir)
    assert q_search["matches"] == 1
    chat = get_chat(q_search["results"][0]["chat_id"], base_dir=db_dir)
    assert chat is not None
    assert len(chat["messages"]) == 4

    assert search_chats("Baratza DF54", base_dir=db_dir)["matches"] == 1
    assert search_chats("Char Bar Kansas City", base_dir=db_dir)["matches"] == 1


def test_import_aistudio_and_notebooklm_and_exports(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    export_dir = tmp_path / "exports"
    export_dir.mkdir()

    aistudio_file = export_dir / "System Architecture Prompt"
    aistudio_file.write_text(
        json.dumps(
            {
                "runSettings": {"model": "models/gemini-2.5-pro"},
                "systemInstruction": {"text": "You are a distributed systems architect."},
                "chunkedPrompt": {
                    "chunks": [
                        {"role": "user", "text": "Design a multi-region active-active database."},
                        {
                            "role": "model",
                            "text": "Let's evaluate CockroachDB and Spanner.",
                            "isThought": True,
                        },
                        {"role": "model", "text": "Use Cloud Spanner or CockroachDB with Raft."},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    aistudio_api_file = export_dir / "api_prompt.prompt"
    aistudio_api_file.write_text(
        json.dumps(
            {
                "name": "API Prompt Test",
                "runSettings": {"model": "models/gemini-2.5-flash"},
                "contents": [
                    {"role": "user", "parts": [{"text": "Write a SQLite FTS5 query."}]},
                    {
                        "role": "model",
                        "parts": [
                            {"text": "SELECT * FROM chats_fts WHERE chats_fts MATCH 'query';"}
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    html_file = export_dir / "MyActivity.html"
    html_file.write_text(
        """
        <html><body>
        <div class="outer-cell">
            <div class="header-cell">AI Mode</div>
            <div class="content-cell">
                Asked in AI Mode How to deploy Cloud Run
                Use gcloud run deploy --source .
                2026-11-05T09:00:00Z
            </div>
        </div>
        </body></html>
        """,
        encoding="utf-8",
    )

    ext_json = export_dir / "chat_export.json"
    ext_json.write_text(
        json.dumps(
            {
                "id": "ext_chat_01",
                "title": "Kubernetes Debugging",
                "messages": [
                    {"role": "user", "content": "Why is my pod in CrashLoopBackOff?"},
                    {"role": "assistant", "content": "Check kubectl logs --previous."},
                ],
            }
        ),
        encoding="utf-8",
    )

    nb_dir = export_dir / "NotebookLM"
    nb_dir.mkdir()
    nb_file = nb_dir / "Research_Notes.html"
    nb_file.write_text(
        "<html><body><h1>Paper Summary</h1><p>Key transformer insights.</p></body></html>",
        encoding="utf-8",
    )

    res = import_from_path(export_dir, base_dir=db_dir)
    assert res["imported_count"] == 5

    assert list_chats(source="aistudio", base_dir=db_dir)["returned"] == 2
    assert search_chats("CockroachDB Spanner", base_dir=db_dir)["matches"] == 1
    assert search_chats("CrashLoopBackOff", base_dir=db_dir)["matches"] == 1
    assert search_chats("gcloud run deploy", base_dir=db_dir)["matches"] == 1
    assert search_chats("transformer insights", base_dir=db_dir)["matches"] == 1
    assert "error" in import_from_path(tmp_path / "missing_folder", base_dir=db_dir)
