import json
import zipfile
from pathlib import Path

from gemini_web_chats.db import get_chat, list_chats, search_chats
from gemini_web_chats.importers import import_from_path


def test_import_takeout_json_and_zip(tmp_path: Path) -> None:
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
            "header": "Gemini Apps",
            "title": "Prompted Best sourdough bread recipe",
            "time": "2026-11-02T15:30:00.000Z",
            "safeHtmlItem": [
                {"html": "<p>Use 500g bread flour, 350g water, 100g starter, 10g salt.</p>"}
            ],
        },
    ]

    json_file = src_dir / "MyActivity.json"
    json_file.write_text(json.dumps(takeout_data), encoding="utf-8")

    # Also test zip import containing MyActivity.json
    zip_file = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zip_file, "w") as zf:
        zf.write(json_file, arcname="Takeout/My Activity/Gemini Apps/MyActivity.json")

    res = import_from_path(zip_file, base_dir=db_dir)
    assert res["imported_count"] == 2  # Two threads (separated by >45 mins)

    listed = list_chats(base_dir=db_dir)
    assert listed["total_chats"] == 2

    q_search = search_chats("Qiskit qubits", base_dir=db_dir)
    assert q_search["matches"] == 1
    chat = get_chat(q_search["results"][0]["chat_id"], base_dir=db_dir)
    assert chat is not None
    assert len(chat["messages"]) == 4  # 2 prompts + 2 responses grouped into 1 thread


def test_import_takeout_html_and_exports(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    export_dir = tmp_path / "exports"
    export_dir.mkdir()

    # 1. Takeout HTML
    html_file = export_dir / "MyActivity.html"
    html_file.write_text(
        """
        <html><body>
        <div class="outer-cell">
            <div class="content-cell">
                Prompted How to deploy Cloud Run
                Use gcloud run deploy --source .
                2026-11-05T09:00:00Z
            </div>
        </div>
        </body></html>
        """,
        encoding="utf-8",
    )

    # 2. Extension JSON export
    ext_json = export_dir / "chat_export.json"
    ext_json.write_text(
        json.dumps(
            {
                "id": "ext_chat_01",
                "title": " Kubernetes Debugging ",
                "messages": [
                    {"role": "user", "content": "Why is my pod in CrashLoopBackOff?"},
                    {"role": "assistant", "content": "Check kubectl logs --previous."},
                ],
            }
        ),
        encoding="utf-8",
    )

    # 3. Markdown file
    md_file = export_dir / "notes.md"
    md_file.write_text("# Terraform State Locking\nUse DynamoDB or GCS backend.", encoding="utf-8")

    res = import_from_path(export_dir, base_dir=db_dir)
    assert res["imported_count"] == 3

    assert search_chats("CrashLoopBackOff", base_dir=db_dir)["matches"] == 1
    assert search_chats("gcloud run deploy", base_dir=db_dir)["matches"] == 1
    assert search_chats("Terraform", base_dir=db_dir)["matches"] == 1
    assert "error" in import_from_path(tmp_path / "missing_folder", base_dir=db_dir)
