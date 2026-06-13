import os
from dotenv import load_dotenv

load_dotenv()

API_KEY        = os.getenv("API_KEY")
QDRANT_URL     = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
NEO4J_URI      = os.getenv("NEO4J_URI", "").strip()
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j").strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()