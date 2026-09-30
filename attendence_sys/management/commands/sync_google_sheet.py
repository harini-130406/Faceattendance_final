from django.core.management.base import BaseCommand, CommandError
from attendence_sys.google_sheets_service import sync_google_sheet

class Command(BaseCommand):
    help = 'Synchronizes student registration data from Google Sheet into Django database'

    def add_arguments(self, parser):
        parser.add_argument(
            'sheet_source',
            nargs='?',
            type=str,
            help='Google Sheet URL, Spreadsheet ID, or local CSV file path'
        )

    def handle(self, *args, **options):
        sheet_source = options.get('sheet_source')
        if not sheet_source:
            raise CommandError("Please provide a Google Sheet URL, Spreadsheet ID, or CSV file path.\nExample: python manage.py sync_google_sheet https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit")

        self.stdout.write(self.style.NOTICE("Google Sheet synchronization started...\n"))

        try:
            stats = sync_google_sheet(sheet_source)
            
            self.stdout.write(f"Rows found: {stats['total_rows']}\n")

            for log in stats['logs']:
                self.stdout.write(log)

            self.stdout.write(self.style.SUCCESS("\nSynchronization completed."))
            self.stdout.write(f"Students created: {stats['created']}")
            self.stdout.write(f"Students updated: {stats['updated']}")
            self.stdout.write(f"Photos processed: {stats['photos_processed']}")
            self.stdout.write(f"Errors: {stats['errors']}")

        except Exception as e:
            raise CommandError(f"Synchronization failed: {e}")
