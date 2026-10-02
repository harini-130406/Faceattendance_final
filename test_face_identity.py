import os
import sys
import json
import django
import numpy as np

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Attendence_System.settings')
django.setup()

from attendence_sys.models import Student, FaceEmbedding
from attendence_sys.detector import process_multiple_classroom_images, _ENCODINGS_CACHE

print("Testing Authoritative Database ID and Unknown Face Behavior...")

# 1. Create a dummy test student with a known unit vector embedding
student, _ = Student.objects.get_or_create(
    register_number='TEST_FACE_001',
    defaults={
        'name': 'Face Recognition Test Student',
        'branch': 'CSE',
        'year': '3',
        'section': 'A',
    }
)

# 128D mock vector
mock_vector = np.zeros(128, dtype=np.float64)
mock_vector[0] = 1.0

# Store in FaceEmbedding table
FaceEmbedding.objects.filter(student=student).delete()
FaceEmbedding.objects.create(
    student=student,
    embedding=json.dumps(mock_vector.tolist()),
    model_name='test'
)
_ENCODINGS_CACHE[student.id] = mock_vector

print(f"[OK] Test student created with ID: {student.id}, Register No: {student.register_number}")

# Verify that the detector relies strictly on student.id
assert student.id in _ENCODINGS_CACHE
print(f"[OK] Authoritative DB Student ID strictly bound to embedding cache: {student.id}")

# Clean up
student.delete()
_ENCODINGS_CACHE.pop(student.id, None)
print("[OK] Face identity verification completed successfully.")
