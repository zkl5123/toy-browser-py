"""Run the regression suite stored in tests/."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "tests" / "run.py"), run_name="__main__")
