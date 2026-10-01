from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.loginPage, name='login'),
    path('register/', views.registerPage, name='register'),
    path('logout/', views.logoutUser, name='logout'),
    path('password-reset/', views.passwordResetPage, name='password_reset'),
    path('account/', views.facultyProfile, name='account'),
    path('account/change-password/', views.changePasswordView, name='change_password'),

    # Attendance URLs
    path('attendance/take/', views.takeAttendancePage, name='take_attendance'),
    path('attendance/live/', views.liveAttendancePage, name='live_attendance'),
    path('attendance/live-scan/', views.liveScanFrame, name='live_scan_frame'),
    path('attendance/live-save/', views.liveSaveAttendance, name='live_save_attendance'),
    path('attendance/roster-json/', views.getClassRoster, name='class_roster_json'),
    path('attendance/edit/', views.editAttendance, name='edit_attendance'),
    path('searchattendence/', views.searchAttendence, name='searchattendence'),
    path('attendence/', views.takeAttendence, name='attendence'), # Legacy compatibility

    # Student URLs
    path('students/', views.studentDirectory, name='student_directory'),
    path('students/add/', views.addStudentPage, name='add_student'),
    path('students/<int:student_id>/edit/', views.editStudent, name='edit_student'),
    path('students/<int:student_id>/delete/', views.deleteStudent, name='delete_student'),
    path('students/bulk-delete/', views.bulkDeleteStudents, name='bulk_delete_students'),
    path('students/template/export/', views.downloadExcelTemplateView, name='download_excel_template'),
    path('updateStudentRedirect/', views.updateStudentRedirect, name='updateStudentRedirect'),
    path('updateStudent/', views.updateStudent, name='updateStudent'),

    # Photos
    path('students/<int:student_id>/photo/', views.studentPrimaryPhoto, name='student_primary_photo'),
    path('photos/<int:photo_id>/', views.studentPhotoById, name='student_photo_by_id'),
]