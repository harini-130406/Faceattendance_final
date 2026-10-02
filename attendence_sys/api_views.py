import os
import io
import re
from datetime import date, datetime

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.files.base import ContentFile
from django.core.mail import send_mail, EmailMultiAlternatives
from django.db import transaction
from django.db.models import Count, Q, Avg, Sum
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from rest_framework import status, permissions, generics, views, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .models import Student, StudentPhoto, Attendence, AttendanceSession, Faculty, FaceEmbedding
from .serializers import (
    UserSerializer,
    FacultySerializer,
    StudentSerializer,
    StudentCreateUpdateSerializer,
    StudentPhotoSerializer,
    AttendanceSessionSerializer,
    AttendanceRecordSerializer,
    AttendanceOverrideSerializer,
    AttendanceFinalizeSerializer,
    LoginSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
)
from .google_drive_helper import fetch_drive_image_bytes, attach_drive_photo_to_student
from .google_sheets_service import sync_google_sheet, normalize_academic_year
from .detector import (
    process_classroom_image,
    process_multiple_classroom_images,
    get_student_face_encoding,
    _ENCODINGS_CACHE,
)
from .views import normalize_date_input


# ==============================================================================
# AUTHENTICATION APIS
# ==============================================================================

class ApiLoginView(views.APIView):
    """
    POST /api/auth/login/
    Authenticates staff/faculty member and returns a persistent auth token.
    Supports flexible login by username, email, phone, or name.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        login_input = serializer.validated_data['username'].strip()
        raw_password = serializer.validated_data['password']

        # 1. Flexible case-insensitive lookup
        clean_input = re.sub(r'\s+', '_', login_input)
        matched_user = User.objects.filter(
            Q(username__iexact=login_input) |
            Q(username__iexact=clean_input) |
            Q(email__iexact=login_input) |
            Q(first_name__iexact=login_input) |
            Q(faculty__firstname__iexact=login_input) |
            Q(faculty__email__iexact=login_input) |
            Q(faculty__phone__iexact=login_input)
        ).first()

        user = None
        if matched_user:
            if matched_user.check_password(raw_password) or matched_user.check_password(raw_password.strip()):
                user = matched_user
            else:
                user = authenticate(request, username=matched_user.username, password=raw_password)
        else:
            user = authenticate(request, username=login_input, password=raw_password)

        if user is None:
            return Response(
                {'error': 'Invalid credentials. Please verify your username/email and password.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.is_active:
            return Response(
                {'error': 'This account is inactive. Please contact an administrator.'},
                status=status.HTTP_403_FORBIDDEN
            )

        token, _ = Token.objects.get_or_create(user=user)
        faculty = getattr(user, 'faculty', None)

        faculty_data = FacultySerializer(faculty, context={'request': request}).data if faculty else None
        user_data = UserSerializer(user).data

        return Response({
            'message': 'Login successful.',
            'token': token.key,
            'user': user_data,
            'faculty': faculty_data,
        }, status=status.HTTP_200_OK)


class ApiLogoutView(views.APIView):
    """
    POST /api/auth/logout/
    Revokes the current staff user's authentication token.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            request.user.auth_token.delete()
        except Exception:
            pass
        return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)


class ApiPasswordResetRequestView(views.APIView):
    """
    POST /api/auth/password-reset/
    Validates registered email and sends a secure password reset link.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email'].strip().lower()
        users = list(User.objects.filter(Q(email__iexact=email) | Q(faculty__email__iexact=email)).distinct())

        if not users:
            # Generic response for privacy/anti-enumeration
            return Response({
                'message': 'If an account is associated with this email address, a password reset link has been dispatched.'
            }, status=status.HTTP_200_OK)

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'proconnect795@gmail.com')

        for u in users:
            token = default_token_generator.make_token(u)
            uid = urlsafe_base64_encode(force_bytes(u.pk))
            reset_url = request.build_absolute_uri(f"/reset/{uid}/{token}/")

            context = {
                'email': u.email or email,
                'user': u,
                'uid': uid,
                'token': token,
                'protocol': 'https' if request.is_secure() else 'http',
                'domain': request.get_host(),
                'reset_url': reset_url,
            }

            subject = "Smart Attendance System - Password Reset Request"
            try:
                body = render_to_string('attendence_sys/password_reset_email.html', context)
            except Exception:
                body = f"Hello {u.username},\n\nPlease use the following link to reset your password:\n{reset_url}\n\nThis link will expire in 24 hours."

            try:
                msg = EmailMultiAlternatives(subject, body, from_email, [u.email or email])
                msg.attach_alternative(body, "text/html")
                msg.send(fail_silently=False)
            except Exception as e:
                if settings.DEBUG:
                    return Response({
                        'message': 'Password reset link generated (SMTP note: check console/settings).',
                        'uidb64': uid,
                        'token': token,
                        'reset_url': reset_url,
                        'debug_smtp_error': str(e),
                    }, status=status.HTTP_200_OK)

        return Response({
            'message': 'If an account is associated with this email address, a password reset link has been dispatched.'
        }, status=status.HTTP_200_OK)


class ApiPasswordResetConfirmView(views.APIView):
    """
    POST /api/auth/password-reset-confirm/
    Validates uidb64 and token, and sets the new password.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        uidb64 = serializer.validated_data['uidb64']
        token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response({'error': 'Invalid or corrupted reset link.'}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, token):
            return Response({'error': 'Password reset token has expired or is invalid.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()

        # Invalidate existing auth tokens on password reset for security
        Token.objects.filter(user=user).delete()
        new_auth_token = Token.objects.create(user=user)

        return Response({
            'message': 'Password has been reset successfully. You can now log in with your new credentials.',
            'token': new_auth_token.key,
        }, status=status.HTTP_200_OK)


class ApiCurrentUserView(views.APIView):
    """
    GET /api/auth/me/
    Returns the currently logged-in faculty/staff profile details.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        faculty = getattr(request.user, 'faculty', None)
        return Response({
            'user': UserSerializer(request.user).data,
            'faculty': FacultySerializer(faculty, context={'request': request}).data if faculty else None,
        })


# ==============================================================================
# STUDENT APIS
# ==============================================================================

class StudentListCreateAPIView(views.APIView):
    """
    GET /api/students/
      Filter by: branch, year, section, q (search query). Supports pagination and ?all=true.
    POST /api/students/
      Add single student with optional profile photo or drive link.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        branch = request.GET.get('branch', '').strip()
        year = request.GET.get('year', '').strip()
        section = request.GET.get('section', '').strip()
        search = request.GET.get('q', '').strip() or request.GET.get('search', '').strip()

        qs = Student.objects.all().order_by('branch', 'year', 'section', 'register_number')

        if branch:
            qs = qs.filter(Q(branch__iexact=branch) | Q(department__iexact=branch))
        if year:
            qs = qs.filter(year=str(year))
        if section:
            qs = qs.filter(section__iexact=section)
        if search:
            qs = qs.filter(
                Q(register_number__icontains=search) |
                Q(registration_id__icontains=search) |
                Q(name__icontains=search) |
                Q(firstname__icontains=search) |
                Q(lastname__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )

        # Unpaginated option for full client roster
        if request.GET.get('all', '').lower() in ('true', '1'):
            serializer = StudentSerializer(qs, many=True, context={'request': request})
            return Response({
                'count': qs.count(),
                'results': serializer.data
            })

        paginator = settings.REST_FRAMEWORK.get('DEFAULT_PAGINATION_CLASS')
        from rest_framework.pagination import PageNumberPagination
        p = PageNumberPagination()
        p.page_size = 50
        page = p.paginate_queryset(qs, request)
        serializer = StudentSerializer(page, many=True, context={'request': request})
        return p.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = StudentCreateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        drive_link = serializer.validated_data.pop('drive_link', '')
        student = serializer.save()

        faculty = getattr(request.user, 'faculty', None)
        faculty_name = str(faculty) if faculty else request.user.username
        student.enrolled_by = request.user
        student.enrolled_by_name = faculty_name
        student.save()

        # Handle drive link if provided
        if drive_link:
            try:
                attach_drive_photo_to_student(student, drive_link, is_primary=True, photo_order=1)
            except Exception as e:
                pass

        # Clear cache for this student
        _ENCODINGS_CACHE.pop(student.id, None)

        return Response(
            StudentSerializer(student, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


class StudentDetailAPIView(views.APIView):
    """
    GET /api/students/<id>/
    PUT/PATCH /api/students/<id>/
    DELETE /api/students/<id>/
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self, pk):
        try:
            return Student.objects.get(pk=pk)
        except Student.DoesNotExist:
            return None

    def get(self, request, pk):
        student = self.get_object(pk)
        if not student:
            return Response({'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(StudentSerializer(student, context={'request': request}).data)

    def put(self, request, pk):
        return self.patch(request, pk)

    def patch(self, request, pk):
        student = self.get_object(pk)
        if not student:
            return Response({'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = StudentCreateUpdateSerializer(student, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        drive_link = serializer.validated_data.pop('drive_link', '')
        updated_student = serializer.save()

        if drive_link:
            try:
                attach_drive_photo_to_student(updated_student, drive_link, is_primary=True, photo_order=1)
            except Exception:
                pass

        _ENCODINGS_CACHE.pop(updated_student.id, None)
        FaceEmbedding.objects.filter(student=updated_student).delete()

        return Response(StudentSerializer(updated_student, context={'request': request}).data)

    def delete(self, request, pk):
        student = self.get_object(pk)
        if not student:
            return Response({'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND)

        _ENCODINGS_CACHE.pop(student.id, None)
        reg_no = student.register_number
        student.delete()
        return Response({'message': f"Student {reg_no} deleted successfully."}, status=status.HTTP_200_OK)


class StudentPhotosListAPIView(views.APIView):
    """
    GET /api/students/<id>/photos/
    Lists all enrolled photos for this student.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            student = Student.objects.get(pk=pk)
        except Student.DoesNotExist:
            return Response({'error': 'Student not found.'}, status=status.HTTP_404_NOT_FOUND)

        photos = student.photos.all()
        serializer = StudentPhotoSerializer(photos, many=True, context={'request': request})
        return Response({
            'student_id': student.id,
            'register_number': student.register_number,
            'total_photos': photos.count(),
            'photos': serializer.data,
        })


class StudentPhotoProxyAPIView(views.APIView):
    """
    GET /api/students/<id>/photo/
    Securely serves the student's actual photo bytes.
    If the photo is on Google Drive, Django proxies the image bytes
    so that Google Drive private credentials are NEVER exposed to the mobile app.
    Falls back to local file or default avatar.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            student = Student.objects.get(pk=pk)
        except Student.DoesNotExist:
            return HttpResponse(status=404)

        # 1. Check primary StudentPhoto from Google Drive
        primary_photo = student.get_primary_photo()
        if primary_photo and primary_photo.drive_file_id:
            img_bytes, content_type = fetch_drive_image_bytes(primary_photo.drive_file_id)
            if img_bytes:
                return HttpResponse(img_bytes, content_type=content_type)

        # 2. Check local uploaded profile_pic
        if student.profile_pic and os.path.exists(student.profile_pic.path):
            try:
                with open(student.profile_pic.path, 'rb') as f:
                    ext = student.profile_pic.path.split('.')[-1].lower()
                    ctype = 'image/png' if ext == 'png' else 'image/jpeg'
                    return HttpResponse(f.read(), content_type=ctype)
            except Exception:
                pass

        # 3. Fallback to default avatar asset
        placeholder_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'default_avatar.png')
        if os.path.exists(placeholder_path):
            with open(placeholder_path, 'rb') as f:
                return HttpResponse(f.read(), content_type='image/png')

        return HttpResponse(status=404)


# ==============================================================================
# EXCEL STUDENT IMPORT API
# ==============================================================================

class ExcelStudentImportAPIView(views.APIView):
    """
    POST /api/students/import-excel/
    Accepts multipart/form-data with .xlsx, .xls, or .csv file.
    Idempotent: Re-importing does NOT duplicate students (matched on register_number).
    Returns detailed summary: created, updated, skipped, errors, logs.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return Response(
                {'error': "No file provided. Please upload an Excel (.xlsx/.xls) or CSV file with the form key 'file'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        default_year = request.data.get('default_year')
        default_branch = request.data.get('default_branch')
        default_section = request.data.get('default_section')
        drive_folder_url = request.data.get('drive_folder_url')

        faculty = getattr(request.user, 'faculty', None)
        faculty_name = str(faculty) if faculty else request.user.username

        try:
            stats = sync_google_sheet(
                uploaded_file,
                download_photos=True,
                drive_folder_url=drive_folder_url if drive_folder_url else None,
                enrolled_by=request.user,
                enrolled_by_name=faculty_name,
                default_year=default_year,
                default_branch=default_branch,
                default_section=default_section,
            )

            # Invalidate encoding cache after import so newly imported students/photos are picked up
            _ENCODINGS_CACHE.clear()

            return Response({
                'success': True,
                'message': 'Excel student import processed successfully.',
                'total_rows': stats.get('total_rows', 0),
                'created': stats.get('created', 0),
                'updated': stats.get('updated', 0),
                'skipped': stats.get('total_rows', 0) - (stats.get('created', 0) + stats.get('updated', 0)),
                'photos_processed': stats.get('photos_processed', 0),
                'errors': stats.get('errors', 0),
                'logs': stats.get('logs', []),
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({
                'success': False,
                'error': f"Failed to process student import: {str(e)}"
            }, status=status.HTTP_400_BAD_REQUEST)


# ==============================================================================
# ATTENDANCE APIS
# ==============================================================================

class AttendanceProcessAPIView(views.APIView):
    """
    POST /api/attendance/process/
    Processes classroom photo(s) captured by mobile camera.
    Face Recognition Identity Flow:
      Classroom Image -> Multi-Scale Face Detection -> 128D Face Embedding
      -> Compare against CURRENT DB embeddings (authoritative Student.id)
      -> 1-to-1 bipartite matching with margin filtering & calibrated tolerance (0.48)
      -> Match below tolerance = Present; unmatched / low confidence = UNKNOWN
      -> DB lookup -> Student Name -> Attendance Session
    Returns structured results with session_id, student roster, confidence scores, and unknown_faces count.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        # Support single 'classroom_image' or multiple 'classroom_images'
        classroom_files = request.FILES.getlist('classroom_images')
        if not classroom_files:
            single = request.FILES.get('classroom_image')
            if single:
                classroom_files = [single]

        if not classroom_files:
            return Response(
                {'error': "No classroom image provided. Please upload 'classroom_image' or 'classroom_images'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        branch = request.data.get('branch', 'CSE').strip()
        year = request.data.get('year', '1').strip()
        section = request.data.get('section', 'A').strip().upper()
        period = request.data.get('period', '1').strip()
        raw_date = request.data.get('date', '').strip()
        date_obj, date_iso = normalize_date_input(raw_date)

        try:
            tolerance = float(request.data.get('tolerance', 0.48))
        except (ValueError, TypeError):
            tolerance = 0.48

        faculty = getattr(request.user, 'faculty', None)
        faculty_name = str(faculty) if faculty else request.user.username

        # 1. Run AI face detection & recognition across uploaded photos
        try:
            detection_result = process_multiple_classroom_images(
                classroom_files,
                branch=branch,
                year=year,
                section=section,
                tolerance=tolerance
            )
        except Exception as e:
            return Response({
                'error': f"Face recognition processing failed: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 2. Create or fetch AttendanceSession
        session, _ = AttendanceSession.objects.get_or_create(
            date=date_obj,
            department=branch,
            year=year,
            section=section,
            defaults={'teacher': faculty_name, 'subject': f"{branch} Period {period}"}
        )

        try:
            first_file = classroom_files[0]
            first_file.seek(0)
            session.classroom_image.save(
                f"classroom_{branch}_{year}_{section}_{period}_{date_iso}.jpg",
                first_file,
                save=True
            )
        except Exception:
            pass

        # 3. Format structured student roster for mobile client
        results = []
        for r in detection_result['roster']:
            student = r['student']
            photo_url = f"/api/students/{student.id}/photo/"
            results.append({
                'student_id': student.id,
                'register_number': student.register_number or student.registration_id,
                'name': student.name or f"{student.firstname or ''} {student.lastname or ''}".strip(),
                'status': r['status'],
                'confidence': r['confidence'],
                'dual_confirmed': r.get('dual_confirmed', False),
                'photos_seen_count': r.get('photos_seen_count', 0),
                'detected_in': r.get('detected_in'),
                'photo_url': request.build_absolute_uri(photo_url),
                'has_enrolled_photo': r.get('has_photo', False),
            })

        total_detected = detection_result['total_detected_faces']
        present_count = detection_result['present_count']
        unknown_faces = max(0, total_detected - present_count)

        return Response({
            'session_id': session.id,
            'date': date_iso,
            'branch': branch,
            'year': year,
            'section': section,
            'period': period,
            'total_enrolled': detection_result['total_enrolled'],
            'total_detected_faces': total_detected,
            'recognized_count': present_count,
            'unknown_faces': unknown_faces,
            'present_count': present_count,
            'absent_count': detection_result['absent_count'],
            'attendance_rate': detection_result['attendance_rate'],
            'results': results,
            'annotated_images': [img.get('image_b64') for img in detection_result.get('annotated_images', []) if img.get('image_b64')],
        }, status=status.HTTP_200_OK)


class AttendanceSessionDetailAPIView(views.APIView):
    """
    GET /api/attendance/session/<id>/
    Retrieves the attendance roster and metadata for an attendance session.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            session = AttendanceSession.objects.get(pk=pk)
        except AttendanceSession.DoesNotExist:
            return Response({'error': 'Attendance session not found.'}, status=status.HTTP_404_NOT_FOUND)

        records = Attendence.objects.filter(session=session).order_by('Student_ID')
        session_data = AttendanceSessionSerializer(session, context={'request': request}).data
        records_data = AttendanceRecordSerializer(records, many=True, context={'request': request}).data

        return Response({
            'session': session_data,
            'records': records_data,
        })


class AttendanceSessionFinalizeAPIView(views.APIView):
    """
    POST /api/attendance/session/<id>/finalize/
    Finalizes and saves the verified attendance results into the database.
    Accepts:
      {
        "period": "1",
        "date": "2026-10-02",
        "records": [
          {"student_id": "10", "status": "Present", "confidence": 95.0},
          {"student_id": "11", "status": "Absent", "confidence": 0.0}
        ]
      }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            session = AttendanceSession.objects.get(pk=pk)
        except AttendanceSession.DoesNotExist:
            return Response({'error': 'Attendance session not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AttendanceFinalizeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        period = serializer.validated_data['period']
        raw_date = serializer.validated_data.get('date') or str(session.date)
        date_obj, date_iso = normalize_date_input(raw_date)

        faculty = getattr(request.user, 'faculty', None)
        faculty_name = str(faculty) if faculty else request.user.username

        records_data = serializer.validated_data['records']
        saved_present = 0
        saved_absent = 0

        with transaction.atomic():
            for item in records_data:
                s_ident = str(item['student_id']).strip()
                status_val = item['status']
                confidence = float(item.get('confidence', 0.0))

                # Lookup by DB pk first, then by register_number
                student_obj = None
                if s_ident.isdigit():
                    student_obj = Student.objects.filter(id=int(s_ident)).first()
                if not student_obj:
                    student_obj = Student.objects.filter(
                        Q(register_number=s_ident) | Q(registration_id=s_ident)
                    ).first()

                reg_no = student_obj.register_number if student_obj else s_ident

                rec, _ = Attendence.objects.update_or_create(
                    date=date_obj,
                    branch=session.department,
                    year=session.year,
                    section=session.section,
                    period=period,
                    Student_ID=reg_no,
                    defaults={
                        'session': session,
                        'student_ref': student_obj,
                        'Faculty_Name': faculty_name,
                        'status': status_val,
                        'confidence': confidence if status_val == 'Present' else 0.0,
                    }
                )

                if status_val == 'Present':
                    saved_present += 1
                else:
                    saved_absent += 1

        return Response({
            'success': True,
            'message': f"Attendance finalized successfully: {saved_present} Present, {saved_absent} Absent.",
            'session_id': session.id,
            'date': date_iso,
            'period': period,
            'saved_present': saved_present,
            'saved_absent': saved_absent,
            'total_saved': saved_present + saved_absent,
        }, status=status.HTTP_200_OK)


class AttendanceOverrideAPIView(views.APIView):
    """
    POST /api/attendance/<id>/override/
    Staff attendance correction: flips or updates status between Present <-> Absent.
    Persists immediately in the Django database.
    Accepts: {"status": "Present"} or {"status": "Absent"}
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            record = Attendence.objects.get(pk=pk)
        except Attendence.DoesNotExist:
            return Response({'error': 'Attendance record not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = AttendanceOverrideSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'errors': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        new_status = serializer.validated_data['status']
        confidence = serializer.validated_data.get('confidence', 100.0 if new_status == 'Present' else 0.0)

        record.status = new_status
        record.confidence = confidence
        faculty = getattr(request.user, 'faculty', None)
        if faculty:
            record.Faculty_Name = str(faculty)
        record.save()

        return Response({
            'success': True,
            'message': f"Attendance updated to '{new_status}' for {record.Student_ID}.",
            'record': AttendanceRecordSerializer(record, context={'request': request}).data,
        }, status=status.HTTP_200_OK)


class AttendanceHistoryAPIView(views.APIView):
    """
    GET /api/attendance/history/
    Search and filter past attendance records with granular query parameters:
      - date (YYYY-MM-DD)
      - start_date & end_date
      - branch / department
      - year
      - section
      - period
      - status (Present / Absent)
      - student_id / register_number
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Attendence.objects.all().order_by('-date', '-period', 'branch', 'section', 'Student_ID')

        raw_date = request.GET.get('date', '').strip()
        if raw_date:
            _, date_iso = normalize_date_input(raw_date)
            qs = qs.filter(date=date_iso)

        start_date = request.GET.get('start_date', '').strip()
        if start_date:
            _, s_iso = normalize_date_input(start_date)
            qs = qs.filter(date__gte=s_iso)

        end_date = request.GET.get('end_date', '').strip()
        if end_date:
            _, e_iso = normalize_date_input(end_date)
            qs = qs.filter(date__lte=e_iso)

        branch = request.GET.get('branch', '').strip()
        if branch:
            qs = qs.filter(Q(branch__iexact=branch) | Q(student_ref__department__iexact=branch))

        year = request.GET.get('year', '').strip()
        if year:
            qs = qs.filter(year=str(year))

        section = request.GET.get('section', '').strip()
        if section:
            qs = qs.filter(section__iexact=section)

        period = request.GET.get('period', '').strip()
        if period:
            qs = qs.filter(period=period)

        att_status = request.GET.get('status', '').strip()
        if att_status:
            qs = qs.filter(status__iexact=att_status)

        reg_search = request.GET.get('student_id', '').strip() or request.GET.get('register_number', '').strip()
        if reg_search:
            qs = qs.filter(Q(Student_ID__icontains=reg_search) | Q(student_ref__register_number__icontains=reg_search))

        # Overall summary stats
        p_count = qs.filter(status__iexact='Present').count()
        a_count = qs.filter(status__iexact='Absent').count()
        tot = p_count + a_count
        pct = round(p_count / tot * 100, 1) if tot > 0 else 0.0

        if request.GET.get('all', '').lower() in ('true', '1'):
            return Response({
                'total_records': tot,
                'present_count': p_count,
                'absent_count': a_count,
                'attendance_percentage': pct,
                'results': AttendanceRecordSerializer(qs, many=True, context={'request': request}).data
            })

        from rest_framework.pagination import PageNumberPagination
        p = PageNumberPagination()
        p.page_size = 50
        page = p.paginate_queryset(qs, request)
        serializer = AttendanceRecordSerializer(page, many=True, context={'request': request})
        res = p.get_paginated_response(serializer.data)
        res.data['summary'] = {
            'total_records': tot,
            'present_count': p_count,
            'absent_count': a_count,
            'attendance_percentage': pct,
        }
        return res


# ==============================================================================
# REPORT API
# ==============================================================================

class AttendanceReportsAPIView(views.APIView):
    """
    GET /api/attendance/reports/
    Aggregates attendance analytics for Flutter dashboards:
      - Attendance percentage
      - Present count & Absent count
      - Department-wise breakdown
      - Year-wise breakdown
      - Student-wise attendance percentage
      - Date / month range filtering
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Attendence.objects.all()

        start_date = request.GET.get('start_date', '').strip()
        if start_date:
            _, s_iso = normalize_date_input(start_date)
            qs = qs.filter(date__gte=s_iso)

        end_date = request.GET.get('end_date', '').strip()
        if end_date:
            _, e_iso = normalize_date_input(end_date)
            qs = qs.filter(date__lte=e_iso)

        branch = request.GET.get('branch', '').strip()
        if branch:
            qs = qs.filter(branch__iexact=branch)

        year = request.GET.get('year', '').strip()
        if year:
            qs = qs.filter(year=str(year))

        section = request.GET.get('section', '').strip()
        if section:
            qs = qs.filter(section__iexact=section)

        # 1. High-level aggregates
        total_records = qs.count()
        present_count = qs.filter(status__iexact='Present').count()
        absent_count = qs.filter(status__iexact='Absent').count()
        overall_percentage = round((present_count / total_records * 100), 1) if total_records > 0 else 0.0

        # 2. Department-wise breakdown
        dept_breakdown = []
        depts = qs.values('branch').annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status__iexact='Present')),
            absent=Count('id', filter=Q(status__iexact='Absent')),
        ).order_by('branch')
        for d in depts:
            b_name = d['branch'] or 'General'
            tot = d['total']
            p = d['present']
            pct = round(p / tot * 100, 1) if tot > 0 else 0.0
            dept_breakdown.append({
                'department': b_name,
                'total_classes': tot,
                'present_count': p,
                'absent_count': d['absent'],
                'percentage': pct,
            })

        # 3. Year-wise breakdown
        year_breakdown = []
        years = qs.values('year').annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status__iexact='Present')),
            absent=Count('id', filter=Q(status__iexact='Absent')),
        ).order_by('year')
        for y in years:
            y_name = y['year'] or 'Unknown'
            tot = y['total']
            p = y['present']
            pct = round(p / tot * 100, 1) if tot > 0 else 0.0
            year_breakdown.append({
                'year': y_name,
                'total_classes': tot,
                'present_count': p,
                'absent_count': y['absent'],
                'percentage': pct,
            })

        # 4. Student-wise breakdown (top or filtered)
        student_breakdown = []
        students_agg = qs.values('Student_ID').annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status__iexact='Present')),
            absent=Count('id', filter=Q(status__iexact='Absent')),
        ).order_by('Student_ID')

        # Prefetch student names for clean reporting
        student_obj_map = {
            s.register_number: s
            for s in Student.objects.filter(register_number__in=[x['Student_ID'] for x in students_agg[:200]])
        }

        for sa in students_agg[:200]:
            s_id = sa['Student_ID']
            tot = sa['total']
            p = sa['present']
            pct = round(p / tot * 100, 1) if tot > 0 else 0.0
            s_obj = student_obj_map.get(s_id)
            name = (s_obj.name or s_obj.firstname) if s_obj else s_id
            student_breakdown.append({
                'student_id': s_obj.id if s_obj else None,
                'register_number': s_id,
                'name': name,
                'total_classes': tot,
                'present_count': p,
                'absent_count': sa['absent'],
                'percentage': pct,
            })

        return Response({
            'overall_summary': {
                'total_records': total_records,
                'present_count': present_count,
                'absent_count': absent_count,
                'attendance_percentage': overall_percentage,
            },
            'department_breakdown': dept_breakdown,
            'year_breakdown': year_breakdown,
            'student_breakdown': student_breakdown,
        })


class DataSyncAPIView(views.APIView):
    """
    Secure endpoint for migrating fixtures/data from local workspace to production.
    Protected by X-Sync-Token header matching SECRET_KEY.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        sync_token = request.headers.get('X-Sync-Token', '')
        expected_token = getattr(settings, 'SYNC_SECRET_TOKEN', '') or settings.SECRET_KEY
        if not sync_token or sync_token != expected_token:
            return Response({'error': 'Unauthorized sync token'}, status=status.HTTP_403_FORBIDDEN)

        data = request.data
        if not data or not isinstance(data, list):
            return Response({'error': 'Fixture data must be a JSON list of objects'}, status=status.HTTP_400_BAD_REQUEST)

        import tempfile
        import json
        from django.core.management import call_command

        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(data, f)
            temp_path = f.name

        try:
            call_command('loaddata', temp_path)
            return Response({
                'status': 'success',
                'message': f'Successfully loaded {len(data)} objects into database.'
            })
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

