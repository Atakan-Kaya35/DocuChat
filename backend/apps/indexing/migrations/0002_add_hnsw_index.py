"""
Migration to add HNSW index on doc_chunks.embedding for fast vector search.

HNSW (Hierarchical Navigable Small World) provides:
- Fast approximate nearest neighbor search
- No need to pre-train like IVFFlat
- Good balance of speed and recall
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('indexing', '0001_initial'),
    ]

    operations = [
        # Create HNSW index for cosine distance (most common for text embeddings)
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS doc_chunks_embedding_hnsw_idx
                ON doc_chunks
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64);
            """,
            reverse_sql="DROP INDEX IF EXISTS doc_chunks_embedding_hnsw_idx;"
        ),
    ]
