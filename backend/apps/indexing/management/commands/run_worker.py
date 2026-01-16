"""
Django management command to run the indexing worker.

Usage:
    python manage.py run_worker
"""
from django.core.management.base import BaseCommand

from apps.indexing.worker import IndexingWorker


class Command(BaseCommand):
    help = 'Run the document indexing worker'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Process one job and exit (for testing)',
        )

    def handle(self, *args, **options):
        worker = IndexingWorker()
        
        if options['once']:
            self.stdout.write('Running worker once...')
            if worker.run_once():
                self.stdout.write(self.style.SUCCESS('Processed one job'))
            else:
                self.stdout.write('No jobs available')
        else:
            self.stdout.write('Starting worker loop...')
            worker.run()
