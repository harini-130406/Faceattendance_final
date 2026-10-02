import os
import shutil
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Non-destructively copies existing media images to settings.MEDIA_ROOT (e.g. Railway Persistent Volume) if not already present."

    def handle(self, *args, **options):
        source_dir = os.path.join(settings.BASE_DIR, 'static', 'images')
        target_dir = os.path.abspath(settings.MEDIA_ROOT)

        if not os.path.exists(source_dir):
            self.stdout.write(self.style.WARNING(f"Source media directory does not exist: {source_dir}"))
            return

        if os.path.abspath(source_dir) == target_dir:
            self.stdout.write(self.style.SUCCESS("Source and target media directories are identical. No seeding required."))
            return

        os.makedirs(target_dir, exist_ok=True)
        copied = 0
        skipped = 0

        # Subdirectories containing media files
        media_subdirs = ['Student_Images', 'Classroom_Images', 'Faculty_Images', 'google_drive_cache']

        for subdir in media_subdirs:
            src_sub = os.path.join(source_dir, subdir)
            if not os.path.exists(src_sub):
                continue

            for root, _, files in os.walk(src_sub):
                for f in files:
                    src_file = os.path.join(root, f)
                    rel = os.path.relpath(src_file, source_dir)
                    dst_file = os.path.join(target_dir, rel)

                    if not os.path.exists(dst_file):
                        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        copied += 1
                    else:
                        skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"[Volume Seed] Completed successfully. {copied} files copied to volume ({target_dir}), {skipped} existing files preserved."
            )
        )
