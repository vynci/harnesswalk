"""The SQLite file behind the semantic, episodic and knowledge stores and the memory writer.

One file (`stores.memory.sqlite_path`, default `data/memory.db`) holds every table of the
three retrieving stores, the writer's proposals, and, with `storage.vectors: sqlite`, the
vectors. Each store module owns its own `SCHEMA`; this module only opens the file with all
of them.
"""

from __future__ import annotations

from harnesswalk.adapters.storage import vectors
from harnesswalk.adapters.storage.sqlite import Database


def open_memory_db(path: str) -> Database:
    from harnesswalk.context import memory_writer
    from harnesswalk.context.stores import episodic, knowledge, semantic

    schema = "\n".join(
        (semantic.SCHEMA, episodic.SCHEMA, knowledge.SCHEMA, memory_writer.SCHEMA, vectors.SCHEMA)
    )
    return Database(path, schema)
