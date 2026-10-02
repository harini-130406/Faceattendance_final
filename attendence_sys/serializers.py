from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q
from .models import Faculty, Student, StudentPhoto, AttendanceSession, Attendence, FaceEmbedding


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_staff']
        read_only_fields = ['id', 'is_staff']


class FacultySerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    full_name = serializers.SerializerMethodField()
    profile_pic_url = serializers.SerializerMethodField()

    class Meta:
        model = Faculty
        fields = ['id', 'user', 'firstname', 'lastname', 'full_name', 'phone', 'email', 'profile_pic', 'profile_pic_url']
        read_only_fields = ['id', 'user']

    def get_full_name(self, obj):
        return str(obj)

    def get_profile_pic_url(self, obj):
        if obj.profile_pic:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.profile_pic.url)
            return obj.profile_pic.url
        return None


class StudentPhotoSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = StudentPhoto
        fields = ['id', 'drive_file_id', 'source_url', 'is_primary', 'photo_order', 'photo_url', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        url = f"/photos/{obj.id}/"
        if request:
            return request.build_absolute_uri(url)
        return url


class StudentSerializer(serializers.ModelSerializer):
    photo_url = serializers.SerializerMethodField()
    has_photo = serializers.SerializerMethodField()
    photos_count = serializers.SerializerMethodField()
    photos = StudentPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = Student
        fields = [
            'id',
            'register_number',
            'registration_id',
            'name',
            'firstname',
            'lastname',
            'department',
            'branch',
            'year',
            'section',
            'email',
            'phone',
            'profile_pic',
            'photo_url',
            'has_photo',
            'photos_count',
            'photos',
            'enrolled_by_name',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'enrolled_by_name']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        url = f"/api/students/{obj.id}/photo/"
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_has_photo(self, obj):
        return bool(obj.profile_pic.name != '' or obj.photos.exists())

    def get_photos_count(self, obj):
        return obj.photos.count()


class StudentCreateUpdateSerializer(serializers.ModelSerializer):
    drive_link = serializers.CharField(required=False, write_only=True, allow_blank=True)

    class Meta:
        model = Student
        fields = [
            'id',
            'register_number',
            'registration_id',
            'name',
            'firstname',
            'lastname',
            'department',
            'branch',
            'year',
            'section',
            'email',
            'phone',
            'profile_pic',
            'drive_link',
        ]
        read_only_fields = ['id']

    def validate_register_number(self, value):
        if not value:
            return value
        s = str(value).strip()
        if s.endswith('.0') and s[:-2].replace('-', '').replace('+', '').isdigit():
            s = s[:-2]
        instance = getattr(self, 'instance', None)
        qs = Student.objects.filter(Q(register_number=s) | Q(registration_id=s))
        if instance:
            qs = qs.exclude(id=instance.id)
        if qs.exists():
            raise serializers.ValidationError(f"Student with Register Number '{s}' already exists.")
        return s


class AttendanceSessionSerializer(serializers.ModelSerializer):
    total_records = serializers.SerializerMethodField()
    present_records = serializers.SerializerMethodField()
    absent_records = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceSession
        fields = [
            'id',
            'date',
            'department',
            'year',
            'section',
            'subject',
            'teacher',
            'classroom_image',
            'created_at',
            'total_records',
            'present_records',
            'absent_records',
        ]
        read_only_fields = ['id', 'created_at']

    def get_total_records(self, obj):
        return obj.attendances.count()

    def get_present_records(self, obj):
        return obj.attendances.filter(status__iexact='Present').count()

    def get_absent_records(self, obj):
        return obj.attendances.filter(status__iexact='Absent').count()


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    register_number = serializers.CharField(source='Student_ID', read_only=True)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model = Attendence
        fields = [
            'id',
            'session',
            'student_ref',
            'register_number',
            'Student_ID',
            'student_name',
            'photo_url',
            'Faculty_Name',
            'date',
            'time',
            'branch',
            'year',
            'section',
            'period',
            'status',
            'confidence',
            'created_at',
        ]
        read_only_fields = ['id', 'time', 'created_at']

    def get_student_name(self, obj):
        if obj.student_ref:
            return str(obj.student_ref.name or obj.student_ref.firstname or obj.Student_ID)
        s = Student.objects.filter(Q(register_number=obj.Student_ID) | Q(registration_id=obj.Student_ID)).first()
        if s:
            return str(s.name or s.firstname or obj.Student_ID)
        return str(obj.Student_ID)

    def get_photo_url(self, obj):
        s_id = None
        if obj.student_ref:
            s_id = obj.student_ref.id
        else:
            s = Student.objects.filter(Q(register_number=obj.Student_ID) | Q(registration_id=obj.Student_ID)).first()
            if s:
                s_id = s.id
        if s_id:
            request = self.context.get('request')
            url = f"/api/students/{s_id}/photo/"
            if request:
                return request.build_absolute_uri(url)
            return url
        return None


class AttendanceOverrideSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['Present', 'Absent'])
    confidence = serializers.FloatField(required=False, default=100.0)


class AttendanceFinalizeItemSerializer(serializers.Serializer):
    student_id = serializers.CharField(required=True)
    status = serializers.ChoiceField(choices=['Present', 'Absent'], default='Absent')
    confidence = serializers.FloatField(required=False, default=0.0)


class AttendanceFinalizeSerializer(serializers.Serializer):
    period = serializers.CharField(required=True)
    date = serializers.CharField(required=False, allow_blank=True)
    records = AttendanceFinalizeItemSerializer(many=True, required=True)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uidb64 = serializers.CharField(required=True)
    token = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, write_only=True)
    new_password_confirm = serializers.CharField(required=True, write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        validate_password(attrs['new_password'])
        return attrs
