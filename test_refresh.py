"""Test script to diagnose refresh issues"""

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
PYTHON_EXECUTABLE = sys.executable
SCRIPT_PATH = ROOT_DIR / "detectAndClean.py"

print(f"Python executable: {PYTHON_EXECUTABLE}")
print(f"Script path: {SCRIPT_PATH}")
print(f"Script exists: {SCRIPT_PATH.exists()}")
print(f"\nAttempting to run script...")

try:
    result = subprocess.run(
        [str(PYTHON_EXECUTABLE), str(SCRIPT_PATH)],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        timeout=300,
        shell=True,  # Use shell on Windows
    )

    print(f"\nReturn code: {result.returncode}")
    print(f"\nSTDOUT (last 500 chars):\n{result.stdout[-500:]}")
    if result.stderr:
        print(f"\nSTDERR:\n{result.stderr}")

except Exception as e:
    print(f"\nException: {e}")
    import traceback

    traceback.print_exc()
