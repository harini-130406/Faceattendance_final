from django.urls import path
from . import api_views

urlpatterns = [
    # Authentication
    path('auth/login/', api_views.ApiLoginView.as_view(), name='api_login'),
    path('auth/logout/', api_views.ApiLogoutView.as_view(), name='api_logout'),
    path('auth/password-reset/', api_views.ApiPasswordResetRequestView.as_view(), name='api_password_reset'),
    path('auth/password-reset-confirm/', api_views.ApiPasswordResetConfirmView.as_view(), name='api_password_reset_confirm'),
    path('auth/me/', api_views.ApiCurrentUserView.as_view(), name='api_current_user'),

    # Student Management
    path('students/', api_views.StudentListCreateAPIView.as_view(), name='api_students'),
    path('students/<int:pk>/', api_views.StudentDetailAPIView.as_view(), name='api_student_detail'),
    path('students/<int:pk>/photos/', api_views.StudentPhotosListAPIView.as_view(), name='api_student_photos'),
    path('students/<int:pk>/photo/', api_views.StudentPhotoProxyAPIView.as_view(), name='api_student_photo_proxy'),
    path('students/import-excel/', api_views.ExcelStudentImportAPIView.as_view(), name='api_student_import_excel'),

    # Attendance
    path('attendance/process/', api_views.AttendanceProcessAPIView.as_view(), name='api_attendance_process'),
    path('attendance/session/<int:pk>/', api_views.AttendanceSessionDetailAPIView.as_view(), name='api_attendance_session_detail'),
    path('attendance/session/<int:pk>/finalize/', api_views.AttendanceSessionFinalizeAPIView.as_view(), name='api_attendance_session_finalize'),
    path('attendance/<int:pk>/override/', api_views.AttendanceOverrideAPIView.as_view(), name='api_attendance_override'),
    path('attendance/history/', api_views.AttendanceHistoryAPIView.as_view(), name='api_attendance_history'),
    path('attendance/reports/', api_views.AttendanceReportsAPIView.as_view(), name='api_attendance_reports'),

    # Data Migration & Sync
    path('sync-data/', api_views.DataSyncAPIView.as_view(), name='api_sync_data'),
]

