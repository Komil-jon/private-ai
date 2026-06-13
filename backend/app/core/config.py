import os
from dotenv import load_dotenv

load_dotenv()

API_KEY       = os.getenv("API_KEY")
QDRANT_URL    = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()