import os
import sys
import io
import json
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Attendence_System.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.authtoken.models import Token
from attendence_sys.models import Faculty, Student, StudentPhoto, Attendence, AttendanceSession, FaceEmbedding
import openpyxl

print("=" * 60)
print("RUNNING SMART ATTENDANCE DJANGO REST API VERIFICATION SUITE")
print("=" * 60)

client = Client()

# 1. SETUP / ENSURE TEST FACULTY USER
test_username = "test_staff_faculty"
test_email = "test_staff@institution.edu"
test_pass = "FacultyPass123!"

user, created = User.objects.get_or_create(username=test_username, defaults={'email': test_email, 'first_name': 'Test', 'last_name': 'Faculty'})
user.set_password(test_pass)
user.is_staff = True
user.save()
faculty, _ = Faculty.objects.get_or_create(user=user, defaults={'firstname': 'Test', 'lastname': 'Faculty', 'email': test_email, 'phone': '9876543210'})

print(f"[OK] Test staff user ready: {user.username} ({user.email})")

# 2. TEST AUTH LOGIN API (POST /api/auth/login/)
login_resp = client.post('/api/auth/login/', data=json.dumps({
    'username': test_username,
    'password': test_pass
}), content_type='application/json')

assert login_resp.status_code == 200, f"Login failed: {login_resp.status_code}, {login_resp.content}"
login_data = login_resp.json()
assert 'token' in login_data, "Token missing in login response"
auth_token = login_data['token']
auth_headers = {'HTTP_AUTHORIZATION': f"Token {auth_token}"}
print(f"[OK] 1. Login API works. Token generated: {auth_token[:8]}...")

# 3. TEST AUTH ME API (GET /api/auth/me/)
me_resp = client.get('/api/auth/me/', **auth_headers)
assert me_resp.status_code == 200, f"Auth me failed: {me_resp.status_code}"
me_data = me_resp.json()
assert me_data['user']['username'] == test_username
print(f"[OK] 2. Current staff profile API (/api/auth/me/) works: {me_data['user']['first_name']}")

# 4. TEST PASSWORD RESET REQUEST API (POST /api/auth/password-reset/)
reset_resp = client.post('/api/auth/password-reset/', data=json.dumps({
    'email': test_email
}), content_type='application/json')
assert reset_resp.status_code == 200, f"Password reset request failed: {reset_resp.status_code}"
print("[OK] 3. Password reset request API (/api/auth/password-reset/) works.")

# 5. TEST PASSWORD RESET CONFIRM API (POST /api/auth/password-reset-confirm/)
uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
token = default_token_generator.make_token(user)
new_test_pass = "NewFacultyPass456!"

confirm_resp = client.post('/api/auth/password-reset-confirm/', data=json.dumps({
    'uidb64': uidb64,
    'token': token,
    'new_password': new_test_pass,
    'new_password_confirm': new_test_pass,
}), content_type='application/json')
assert confirm_resp.status_code == 200, f"Password reset confirm failed: {confirm_resp.status_code}, {confirm_resp.content}"

# Verify login with new password
login_again = client.post('/api/auth/login/', data=json.dumps({
    'username': test_username,
    'password': new_test_pass
}), content_type='application/json')
assert login_again.status_code == 200, "Login with reset password failed"
auth_token = login_again.json()['token']
auth_headers = {'HTTP_AUTHORIZATION': f"Token {auth_token}"}
print("[OK] 4. Password reset confirmation and re-authentication verified.")

# 6. TEST STUDENT CRUD APIS
# Create student (POST /api/students/)
create_resp = client.post('/api/students/', data={
    'register_number': 'TEST99001',
    'name': 'API Test Student 1',
    'branch': 'CSE',
    'year': '3',
    'section': 'A',
    'email': 'test_student1@institution.edu',
    'phone': '9123456780',
}, **auth_headers)
assert create_resp.status_code == 201, f"Student create failed: {create_resp.status_code}, {create_resp.content}"
created_student = create_resp.json()
s_id = created_student['id']
print(f"[OK] 5. Student create API works. Student ID: {s_id}, Reg: {created_student['register_number']}")

# Retrieve student (GET /api/students/<id>/)
detail_resp = client.get(f"/api/students/{s_id}/", **auth_headers)
assert detail_resp.status_code == 200
assert detail_resp.json()['register_number'] == 'TEST99001'
print(f"[OK] 6. Student detail API works for ID {s_id}")

# Update student (PATCH /api/students/<id>/)
patch_resp = client.patch(f"/api/students/{s_id}/", data=json.dumps({
    'name': 'API Test Student One Updated',
    'phone': '9876500001'
}), content_type='application/json', **auth_headers)
assert patch_resp.status_code == 200
assert patch_resp.json()['name'] == 'API Test Student One Updated'
print(f"[OK] 7. Student update API works.")

# Student List API with filtering (GET /api/students/?branch=CSE&year=3&q=TEST99001)
list_resp = client.get(f"/api/students/?branch=CSE&year=3&section=A&q=TEST99001", **auth_headers)
assert list_resp.status_code == 200
list_data = list_resp.json()
assert list_data['count'] >= 1
print(f"[OK] 8. Student list API with filtering/search works. Matched: {list_data['count']}")

# Student unpaginated full list (GET /api/students/?all=true)
all_resp = client.get(f"/api/students/?all=true", **auth_headers)
assert all_resp.status_code == 200
assert 'results' in all_resp.json()
print(f"[OK] 9. Student unpaginated roster API (?all=true) works.")

# 7. TEST STUDENT PHOTO API (GET /api/students/<id>/photo/)
# 7a. Unauthenticated request must be rejected with 401
photo_unauth_resp = client.get(f"/api/students/{s_id}/photo/")
assert photo_unauth_resp.status_code == 401, f"Expected 401 for unauthenticated photo request, got {photo_unauth_resp.status_code}"
print("[OK] 10a. Unauthenticated student photo request correctly rejected (HTTP 401).")

# 7b. Authenticated request must succeed with 200 and image bytes
photo_resp = client.get(f"/api/students/{s_id}/photo/", **auth_headers)
assert photo_resp.status_code == 200, f"Student photo proxy failed: {photo_resp.status_code}"
assert len(photo_resp.content) > 0, "Photo content is empty"
assert photo_resp['Content-Type'].startswith('image/'), f"Invalid Content-Type: {photo_resp['Content-Type']}"
print(f"[OK] 10b. Authenticated student photo proxy API returns valid image bytes ({photo_resp['Content-Type']}).")

# 8. TEST EXCEL IMPORT API (POST /api/students/import-excel/)
# Generate in-memory Excel workbook
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Students"
ws.append(["Register Number", "Name", "Department", "Year", "Section", "Email", "Phone"])
ws.append(["TEST99001", "API Test Student One Updated", "CSE", "3 rd yr", "A", "test_student1@institution.edu", "9876500001"])
ws.append(["TEST99002", "API Test Student 2", "CSE", "3", "A", "test_student2@institution.edu", "9876500002"])
ws.append(["TEST99003", "API Test Student 3", "CSE", "3", "A", "test_student3@institution.edu", "9876500003"])

excel_io = io.BytesIO()
wb.save(excel_io)
excel_io.seek(0)

excel_file = SimpleUploadedFile("test_roster.xlsx", excel_io.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

import_resp = client.post('/api/students/import-excel/', data={'file': excel_file}, **auth_headers)
assert import_resp.status_code == 200, f"Excel import failed: {import_resp.status_code}, {import_resp.content}"
import_stats = import_resp.json()
print(f"[OK] 11. Excel student import API works: Total={import_stats['total_rows']}, Created={import_stats['created']}, Updated={import_stats['updated']}")

# TEST IMPORT IDEMPOTENCY: Re-importing identical excel must NOT duplicate students
excel_file.seek(0)
reimport_resp = client.post('/api/students/import-excel/', data={'file': excel_file}, **auth_headers)
assert reimport_resp.status_code == 200
reimport_stats = reimport_resp.json()
assert reimport_stats['created'] == 0, f"Re-import created duplicate students: {reimport_stats['created']}"
assert reimport_stats['updated'] == 3, f"Expected 3 updated, got: {reimport_stats['updated']}"
print(f"[OK] 12. Idempotency verified: Re-importing created 0 new students, updated existing records.")

# 9. TEST ATTENDANCE IMAGE PROCESSING & FACE RECOGNITION (POST /api/attendance/process/)
# Create a dummy blank/valid image test file
import numpy as np
import cv2

# Create a sample test image (300x300 RGB)
dummy_img = np.zeros((300, 300, 3), dtype=np.uint8)
cv2.putText(dummy_img, "Test Class", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
_, img_enc = cv2.imencode('.jpg', dummy_img)
img_bytes = img_enc.tobytes()

uploaded_img = SimpleUploadedFile("classroom_test.jpg", img_bytes, content_type="image/jpeg")

att_proc_resp = client.post('/api/attendance/process/', data={
    'classroom_image': uploaded_img,
    'branch': 'CSE',
    'year': '3',
    'section': 'A',
    'period': '1',
    'date': '2026-10-02',
    'tolerance': '0.48'
}, **auth_headers)

assert att_proc_resp.status_code == 200, f"Attendance process failed: {att_proc_resp.status_code}, {att_proc_resp.content}"
proc_data = att_proc_resp.json()
session_id = proc_data['session_id']
assert 'results' in proc_data
assert 'total_enrolled' in proc_data
assert 'unknown_faces' in proc_data
print(f"[OK] 13. Attendance processing API works. Session ID: {session_id}, Enrolled: {proc_data['total_enrolled']}, Unknown faces: {proc_data['unknown_faces']}")

# 10. TEST ATTENDANCE SESSION DETAIL API (GET /api/attendance/session/<id>/)
sess_resp = client.get(f"/api/attendance/session/{session_id}/", **auth_headers)
assert sess_resp.status_code == 200
print(f"[OK] 14. Attendance session detail API works.")

# 11. TEST ATTENDANCE FINALIZE API (POST /api/attendance/session/<id>/finalize/)
fin_resp = client.post(f"/api/attendance/session/{session_id}/finalize/", data=json.dumps({
    'period': '1',
    'date': '2026-10-02',
    'records': [
        {'student_id': 'TEST99001', 'status': 'Present', 'confidence': 96.5},
        {'student_id': 'TEST99002', 'status': 'Present', 'confidence': 92.0},
        {'student_id': 'TEST99003', 'status': 'Absent', 'confidence': 0.0},
    ]
}), content_type='application/json', **auth_headers)
assert fin_resp.status_code == 200, f"Finalize failed: {fin_resp.status_code}, {fin_resp.content}"
fin_data = fin_resp.json()
assert fin_data['saved_present'] == 2
assert fin_data['saved_absent'] == 1
print(f"[OK] 15. Attendance session finalize API works: Saved Present: {fin_data['saved_present']}, Saved Absent: {fin_data['saved_absent']}")

# 12. TEST ATTENDANCE OVERRIDE / MANUAL CORRECTION (POST /api/attendance/<id>/override/)
rec = Attendence.objects.filter(Student_ID='TEST99003', date='2026-10-02', period='1').first()
assert rec is not None, "Attendance record not found"
assert rec.status == 'Absent'

override_resp = client.post(f"/api/attendance/{rec.id}/override/", data=json.dumps({
    'status': 'Present',
    'confidence': 100.0
}), content_type='application/json', **auth_headers)
assert override_resp.status_code == 200, f"Override failed: {override_resp.status_code}"
rec.refresh_from_db()
assert rec.status == 'Present', "Status was not updated in DB"
print(f"[OK] 16. Manual attendance correction / override persisted successfully in DB (Absent -> Present).")

# 13. TEST ATTENDANCE HISTORY API (GET /api/attendance/history/)
history_resp = client.get('/api/attendance/history/?branch=CSE&year=3&section=A', **auth_headers)
assert history_resp.status_code == 200
hist_data = history_resp.json()
assert 'summary' in hist_data or 'total_records' in hist_data
print(f"[OK] 17. Attendance history API with query filters works.")

# 14. TEST ATTENDANCE REPORTS API (GET /api/attendance/reports/)
report_resp = client.get('/api/attendance/reports/?branch=CSE', **auth_headers)
assert report_resp.status_code == 200
rep_data = report_resp.json()
assert 'overall_summary' in rep_data
assert 'department_breakdown' in rep_data
assert 'year_breakdown' in rep_data
assert 'student_breakdown' in rep_data
print(f"[OK] 18. Attendance reports analytics API works.")

# 15. TEST AUTH LOGOUT API (POST /api/auth/logout/)
logout_resp = client.post('/api/auth/logout/', **auth_headers)
assert logout_resp.status_code == 200
# Token should now be invalid
bad_auth = client.get('/api/auth/me/', **auth_headers)
assert bad_auth.status_code == 401
print(f"[OK] 19. Logout API successfully invalidates token.")

# 16. TEST EXISTING WEB VIEWS BACKWARD COMPATIBILITY
web_login_resp = client.get('/login/')
assert web_login_resp.status_code == 200
web_reset_resp = client.get('/password-reset/')
assert web_reset_resp.status_code == 200
print(f"[OK] 20. Existing Django Web URLs (/login/, /password-reset/, etc.) fully functional.")

# Cleanup test records
Student.objects.filter(register_number__in=['TEST99001', 'TEST99002', 'TEST99003']).delete()
user.delete()
print("[OK] Test clean-up completed.")

print("=" * 60)
print("ALL 20 API & BACKEND VERIFICATION CHECKS PASSED SUCCESSFULLY!")
print("=" * 60)
sys.exit(0)
