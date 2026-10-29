"""Every test runs offline: hash embeddings, never a local Ollama (SHARED rules for tests)."""

import os

os.environ["HARNESSWALK_EMBEDDINGS"] = "fake"
