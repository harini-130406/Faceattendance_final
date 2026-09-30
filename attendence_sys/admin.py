from django.contrib import admin
from .models import Faculty, Student, StudentPhoto, FaceEmbedding, AttendanceSession, Attendence

class StudentPhotoInline(admin.TabularInline):
    model = StudentPhoto
    extra = 1

class FaceEmbeddingInline(admin.TabularInline):
    model = FaceEmbedding
    extra = 0

@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('register_number', 'name', 'department', 'year', 'section', 'email', 'phone', 'created_at')
    search_fields = ('register_number', 'registration_id', 'name', 'email', 'department')
    list_filter = ('department', 'year', 'section')
    inlines = [StudentPhotoInline, FaceEmbeddingInline]

@admin.register(StudentPhoto)
class StudentPhotoAdmin(admin.ModelAdmin):
    list_display = ('student', 'drive_file_id', 'is_primary', 'photo_order', 'created_at')
    search_fields = ('student__register_number', 'student__name', 'drive_file_id')
    list_filter = ('is_primary', 'photo_order')

@admin.register(FaceEmbedding)
class FaceEmbeddingAdmin(admin.ModelAdmin):
    list_display = ('student', 'model_name', 'created_at')
    search_fields = ('student__register_number', 'student__name', 'model_name')

@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ('date', 'department', 'year', 'section', 'subject', 'teacher', 'created_at')
    search_fields = ('department', 'subject', 'teacher')
    list_filter = ('department', 'year', 'section', 'date')

@admin.register(Attendence)
class AttendenceAdmin(admin.ModelAdmin):
    list_display = ('Student_ID', 'Faculty_Name', 'date', 'time', 'branch', 'year', 'section', 'period', 'status')
    search_fields = ('Student_ID', 'Faculty_Name', 'branch')
    list_filter = ('status', 'branch', 'year', 'section', 'date')

@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
    list_display = ('firstname', 'lastname', 'email', 'phone')
    search_fields = ('firstname', 'lastname', 'email')