"""Enable the pgvector backend on PostgreSQL.

Creates the ``vector`` extension, adds an indexed ``embedding_vec`` column to the
chunk table, backfills it from the existing JSON ``embedding`` values, and builds
an HNSW cosine index. After running this, set ``RAG_VECTOR_BACKEND=pgvector``.

No-op-safe: refuses to run on non-PostgreSQL databases (e.g. SQLite) with a
clear message, so it never corrupts the default setup.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from knowledge.models import DocumentChunk

# pgvector's HNSW/IVFFlat indexes cap at 2000 dimensions for full `vector`.
INDEXABLE_DIM_LIMIT = 2000


class Command(BaseCommand):
    help = "Set up the pgvector column + HNSW index and backfill from JSON embeddings (PostgreSQL only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dim", type=int, default=None,
            help="Embedding dimension. Inferred from existing data if omitted.",
        )

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError(
                f"pgvector requires PostgreSQL; current database is '{connection.vendor}'. "
                "Set DATABASE_URL to a Postgres instance first."
            )

        table = DocumentChunk._meta.db_table
        column = "embedding_vec"
        dim = options["dim"] or self._infer_dim()
        if not dim:
            raise CommandError(
                "Could not infer embedding dimension (no embeddings found). "
                "Ingest at least one document or pass --dim."
            )

        with connection.cursor() as cur:
            self.stdout.write("Creating 'vector' extension...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            self.stdout.write(f"Adding column {column} vector({dim})...")
            cur.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} vector({dim});"
            )

            self.stdout.write("Backfilling vectors from JSON embeddings...")
            cur.execute(
                f"UPDATE {table} SET {column} = embedding::text::vector "
                f"WHERE {column} IS NULL AND embedding IS NOT NULL;"
            )

            if dim <= INDEXABLE_DIM_LIMIT:
                self.stdout.write("Building HNSW cosine index...")
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_{column}_hnsw "
                    f"ON {table} USING hnsw ({column} vector_cosine_ops);"
                )
            else:
                self.stdout.write(self.style.WARNING(
                    f"Dimension {dim} exceeds the {INDEXABLE_DIM_LIMIT}-dim index limit. "
                    "Search will work but without an ANN index (slower). "
                    "Use a 1536-dim embedding model (e.g. text-embedding-3-small) "
                    "or `halfvec` to enable indexing."
                ))

        self.stdout.write(self.style.SUCCESS(
            "\npgvector is ready. Now set RAG_VECTOR_BACKEND=pgvector to use it.\n"
            "Re-run this command after large ingests to backfill new rows."
        ))

    def _infer_dim(self):
        sample = (
            DocumentChunk.objects.exclude(embedding=None)
            .values_list("embedding", flat=True)
            .first()
        )
        return len(sample) if sample else None
