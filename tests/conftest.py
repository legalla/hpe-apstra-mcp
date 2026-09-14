import os
import sys

# Make the hpe-apstra-mcp package (parent of this tests/ dir) importable
# regardless of the directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# core.py / server.py only read these lazily (inside _client()), but set
# harmless defaults so importing them never fails even if a real .env is
# absent in the test environment.
os.environ.setdefault("APSTRA_HOST", "apstra.example.test")
os.environ.setdefault("APSTRA_USERNAME", "test-user")
os.environ.setdefault("APSTRA_PASSWORD", "test-pass")
