# Generated migration for content_hash idempotency

from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add content_hash column for document idempotency.
    
    - SHA-256 hash of file content
    - Nullable for existing documents (backfill on next upload or manual script)
    - Unique constraint per user when not null
    """

    dependencies = [
        ('docs', '0001_initial'),
    ]

    operations = [
        # Add content_hash column (nullable for existing docs)
        migrations.AddField(
            model_name='document',
            name='content_hash',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='SHA-256 hash of file content for deduplication',
                max_length=64,
                null=True,
            ),
        ),
        # Add unique constraint for (owner_user_id, content_hash) when hash is not null
        migrations.AddConstraint(
            model_name='document',
            constraint=models.UniqueConstraint(
                condition=models.Q(content_hash__isnull=False),
                fields=('owner_user_id', 'content_hash'),
                name='unique_user_content_hash',
            ),
        ),
    ]
