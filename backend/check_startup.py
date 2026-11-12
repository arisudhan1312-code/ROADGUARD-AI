"""Quick startup check for ROADGUARD AI backend."""
import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
    from app import create_app
    app = create_app()
    print("SUCCESS: Flask app created without errors.")
    print(f"  Blueprints: {list(app.blueprints.keys())}")
    print("  Run: python app.py  or  waitress-serve --port=5000 app:app")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
