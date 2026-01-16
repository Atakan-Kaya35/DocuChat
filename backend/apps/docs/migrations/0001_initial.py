# Generated migration for Document and IndexJob models

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Document',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('owner_user_id', models.CharField(db_index=True, help_text='Keycloak user ID (sub claim)', max_length=255)),
                ('filename', models.CharField(help_text='Original filename', max_length=255)),
                ('content_type', models.CharField(help_text='MIME type of the file', max_length=100)),
                ('size_bytes', models.PositiveIntegerField(help_text='File size in bytes')),
                ('storage_path', models.CharField(help_text='Path to file on disk (relative to upload root)', max_length=500)),
                ('status', models.CharField(choices=[('UPLOADED', 'Uploaded'), ('QUEUED', 'Queued for indexing'), ('INDEXING', 'Currently indexing'), ('INDEXED', 'Successfully indexed'), ('FAILED', 'Indexing failed')], db_index=True, default='UPLOADED', help_text='Current status in the indexing pipeline', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'documents',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='IndexJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('QUEUED', 'Queued'), ('RUNNING', 'Running'), ('COMPLETE', 'Complete'), ('FAILED', 'Failed')], db_index=True, default='QUEUED', help_text='Current job status', max_length=20)),
                ('stage', models.CharField(choices=[('RECEIVED', 'Received'), ('EXTRACT', 'Extracting text'), ('CHUNK', 'Chunking text'), ('EMBED', 'Generating embeddings'), ('STORE', 'Storing in vector DB')], default='RECEIVED', help_text='Current processing stage', max_length=20)),
                ('progress', models.PositiveSmallIntegerField(default=0, help_text='Progress percentage (0-100)')),
                ('error_message', models.TextField(blank=True, help_text='Error message if job failed', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document', models.ForeignKey(help_text='The document being indexed', on_delete=django.db.models.deletion.CASCADE, related_name='index_jobs', to='docs.document')),
            ],
            options={
                'db_table': 'index_jobs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='document',
            index=models.Index(fields=['owner_user_id', 'created_at'], name='documents_owner_u_5af79c_idx'),
        ),
        migrations.AddIndex(
            model_name='indexjob',
            index=models.Index(fields=['document', 'created_at'], name='index_jobs_documen_3e53ed_idx'),
        ),
    ]
