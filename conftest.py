"""Make the repo root importable for every test runner (python -m pytest, pytest)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
