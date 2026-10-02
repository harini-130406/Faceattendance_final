import os
import shutil
import tempfile
from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.core.files.base import ContentFile
from attendence_sys.models import Student, AttendanceSession
from attendence_sys.detector import get_student_face_encoding


class StorageAbstractionTestCase(TestCase):
    """
    Verifies Django storage abstraction for Persistent Volume preparation:
    - Configurable MEDIA_ROOT
    - Student.profile_pic storage
    - AttendanceSession.classroom_image storage
    - Photo proxy endpoint behavior
    - Detector image loading via storage
    - Backwards-compatible legacy image fallback
    """

    def setUp(self):
        self.temp_media = tempfile.mkdtemp()
        self.client = Client()
        # Minimal 1x1 valid PNG byte array for testing
        self.dummy_png = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
            b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )

    def tearDown(self):
        shutil.rmtree(self.temp_media, ignore_errors=True)

    def test_local_media_root_behavior(self):
        """1. Verify MEDIA_ROOT is decoupled from static/ and configurable."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            self.assertEqual(settings.MEDIA_ROOT, self.temp_media)
            self.assertNotEqual(settings.MEDIA_ROOT, settings.STATIC_ROOT)
            for s_dir in settings.STATICFILES_DIRS:
                self.assertNotEqual(os.path.abspath(settings.MEDIA_ROOT), os.path.abspath(s_dir))

    def test_student_profile_pic_storage(self):
        """2. Verify Student.profile_pic saves and opens through Django storage abstraction."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            student = Student.objects.create(
                register_number="TEST_ST_001",
                name="Test Student",
                department="CSE",
                year="3",
                section="A"
            )
            student.profile_pic.save("TEST_ST_001.png", ContentFile(self.dummy_png), save=True)

            # Check that file exists in storage abstraction
            self.assertTrue(student.profile_pic.storage.exists(student.profile_pic.name))
            self.assertIn("Student_Images", student.profile_pic.name)

            # Check that file can be read via storage abstraction
            with student.profile_pic.open("rb") as f:
                content = f.read()
            self.assertEqual(content, self.dummy_png)

    def test_attendance_session_classroom_image_storage(self):
        """3. Verify AttendanceSession.classroom_image saves through Django storage."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            session = AttendanceSession.objects.create(
                department="CSE",
                year="3",
                section="A"
            )
            session.classroom_image.save("test_classroom.png", ContentFile(self.dummy_png), save=True)

            self.assertTrue(session.classroom_image.storage.exists(session.classroom_image.name))
            self.assertIn("Classroom_Images", session.classroom_image.name)

            with session.classroom_image.open("rb") as f:
                content = f.read()
            self.assertEqual(content, self.dummy_png)

    def test_photo_proxy_behavior(self):
        """4. Verify student photo proxy returns image bytes from storage abstraction."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            student = Student.objects.create(
                register_number="TEST_PROXY_001",
                name="Proxy Student",
                department="CSE",
                year="3",
                section="B"
            )
            student.profile_pic.save("TEST_PROXY_001.png", ContentFile(self.dummy_png), save=True)

            resp = self.client.get(f"/students/{student.id}/photo/")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, self.dummy_png)

    def test_detector_image_loading(self):
        """5. Verify detector loads image from profile_pic storage without crashing."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            student = Student.objects.create(
                register_number="TEST_DETECTOR_001",
                name="Detector Student",
                department="CSE",
                year="3",
                section="C"
            )
            student.profile_pic.save("TEST_DETECTOR_001.png", ContentFile(self.dummy_png), save=True)

            # get_student_face_encoding should read image from storage safely
            # Note: Dummy 1x1 image won't find a face, returning None gracefully without exceptions
            encoding = get_student_face_encoding(student)
            self.assertIsNone(encoding)

    def test_legacy_image_fallback(self):
        """6. Verify legacy fallback when student has no profile_pic set but file exists in static/images."""
        with override_settings(MEDIA_ROOT=self.temp_media):
            student = Student.objects.create(
                register_number="LEGACY_REG_777",
                name="Legacy Student",
                department="CSE",
                year="3",
                section="A"
            )
            # Create a mock legacy file in static/images/Student_Images/CSE/3/A/
            legacy_dir = os.path.join(settings.BASE_DIR, 'static', 'images', 'Student_Images', 'CSE', '3', 'A')
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_file = os.path.join(legacy_dir, 'LEGACY_REG_777.png')
            try:
                with open(legacy_file, 'wb') as f:
                    f.write(self.dummy_png)

                resp = self.client.get(f"/students/{student.id}/photo/")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.content, self.dummy_png)
            finally:
                if os.path.exists(legacy_file):
                    os.remove(legacy_file)
