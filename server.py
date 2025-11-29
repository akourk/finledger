"""
Dashboard Server
================
Lightweight Flask server for the portfolio dashboard.
Provides endpoints for:
- Serving the dashboard
- Triggering data refresh
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="dashboard", static_url_path="")
CORS(app)  # Enable CORS for local development

# Get the project root directory
ROOT_DIR = Path(__file__).parent
PYTHON_EXECUTABLE = sys.executable
SCRIPT_PATH = ROOT_DIR / "detectAndClean.py"


@app.route("/")
def index():
    """Serve the dashboard index page."""
    return send_from_directory("dashboard", "index.html")


@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """
    Trigger data refresh by running detectAndClean.py main function.

    Returns:
        JSON with status and any error messages
    """
    try:
        print(f"\n{'='*60}")
        print(f"REFRESH TRIGGERED at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        # Import and run the main function directly
        import sys

        # Temporarily redirect stdout to capture output
        from io import StringIO

        from fin.main import main

        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()

        try:
            # Call the main function directly
            main()

            # Restore stdout
            sys.stdout = old_stdout
            output = captured_output.getvalue()

            print(f"\n{'='*60}")
            print(f"REFRESH COMPLETED SUCCESSFULLY")
            print(f"{'='*60}\n")

            # Show last few lines of output for debugging
            output_lines = output.strip().split("\n")
            if output_lines:
                print("Last output lines:")
                for line in output_lines[-5:]:
                    print(f"  {line}")

            return jsonify(
                {
                    "status": "success",
                    "message": "Data refreshed successfully",
                    "timestamp": datetime.now().isoformat(),
                    "output_preview": (
                        "\n".join(output_lines[-10:]) if output_lines else "Refresh completed"
                    ),
                }
            )

        except Exception as exec_error:
            # Restore stdout even if there's an error
            sys.stdout = old_stdout
            print(f"ERROR during script execution: {str(exec_error)}")
            import traceback

            traceback.print_exc()

            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Script execution error: {str(exec_error)}",
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
                500,
            )

    except Exception as e:
        print(f"ERROR: Exception during refresh: {str(e)}")
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {"status": "error", "message": str(e), "timestamp": datetime.now().isoformat()}
            ),
            500,
        )


@app.route("/api/status")
def status():
    """
    Get the last generation time from portfolio_summary.js.

    Returns:
        JSON with generation timestamp
    """
    try:
        summary_path = ROOT_DIR / "dashboard" / "data" / "portfolio_summary.js"

        if not summary_path.exists():
            return jsonify({"status": "not_generated", "message": "Data not yet generated"})

        # Read the file and extract GeneratedAt
        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Simple extraction of GeneratedAt field
        if '"GeneratedAt":' in content:
            start = content.index('"GeneratedAt":') + len('"GeneratedAt":')
            end = content.index(",", start)
            timestamp_str = content[start:end].strip().strip('"')

            return jsonify(
                {
                    "status": "success",
                    "generated_at": timestamp_str,
                    "file_modified": datetime.fromtimestamp(
                        summary_path.stat().st_mtime
                    ).isoformat(),
                }
            )
        else:
            return jsonify({"status": "unknown", "message": "Could not find generation timestamp"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


@app.route("/favicon.ico")
def favicon():
    """Return empty response for favicon to avoid 404s."""
    return "", 204


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("Portfolio Dashboard Server")
    print(f"{'='*60}")
    print(f"Root directory: {ROOT_DIR}")
    print(f"Python executable: {PYTHON_EXECUTABLE}")
    print(f"Script path: {SCRIPT_PATH}")
    print(f"\nStarting server at http://localhost:5000")
    print("Press Ctrl+C to stop")
    print(f"{'='*60}\n")

    app.run(host="localhost", port=5000, debug=True)
