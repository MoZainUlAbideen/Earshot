"""Embeddings: text → 384-dim vectors that capture meaning (fastembed, ONNX, no PyTorch).

Queries and passages are embedded differently: BGE models expect a search
instruction on the *query* side, and fastembed's query_embed/passage_embed apply
the right form for each. Vectors are normalized, so cosine distance = 1 - dot product.

`embed_model` is stored with every vector. If the model changes, embed_pending()
re-embeds the old rows, so vectors from two models never get compared (they live in
different spaces). This is embedding versioning.
"""

import psycopg

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
BATCH_SIZE = 64


class Embedder:
    def __init__(self, model_name: str = EMBED_MODEL, model=None):
        self.model_name = model_name
        self._model = model  # injectable for tests

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding  # heavy: import and load lazily

            self._model = TextEmbedding(self.model_name)
        return self._model

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.passage_embed(texts)]

    def query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


def to_pgvector(vec: list[float]) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def embed_pending(conn: psycopg.Connection, embedder: Embedder, batch_size: int = BATCH_SIZE) -> int:
    """Embed passages with no vector, or a vector from a different model. Returns the count.
    Commits per batch, so an interruption keeps finished work (a checkpoint)."""
    done = 0
    while True:
        rows = conn.execute(
            """
            SELECT id, text FROM passages
            WHERE embedding IS NULL OR embed_model IS DISTINCT FROM %s
            ORDER BY id LIMIT %s
            """,
            (embedder.model_name, batch_size),
        ).fetchall()
        if not rows:
            return done
        vectors = embedder.passages([text for _, text in rows])
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE passages SET embedding = %s::vector, embed_model = %s WHERE id = %s",
                    [(to_pgvector(v), embedder.model_name, pid) for (pid, _), v in zip(rows, vectors)],
                )
        done += len(rows)
