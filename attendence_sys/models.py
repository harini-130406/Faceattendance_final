from django.db import models
from django.contrib.auth.models import User


def user_directory_path(instance, filename): 
    name, ext = filename.split(".")
    firstname = instance.firstname or ''
    lastname = instance.lastname or ''
    name = (firstname + lastname) or 'faculty'
    filename = name + '.' + ext 
    return 'Faculty_Images/{}'.format(filename)

class Faculty(models.Model):
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.CASCADE)
    firstname = models.CharField(max_length=200, null=True, blank=True)
    lastname = models.CharField(max_length=200, null=True, blank=True)
    phone = models.CharField(max_length=200, null=True, blank=True)
    email = models.CharField(max_length=200, null=True, blank=True)
    profile_pic = models.ImageField(upload_to=user_directory_path, null=True, blank=True)

    def __str__(self):
        return str((self.firstname or '') + " " + (self.lastname or '')).strip() or "Faculty"


def student_directory_path(instance, filename): 
    ext = filename.split(".")[-1]
    reg = instance.register_number or instance.registration_id or 'unknown'
    filename = f"{reg}.{ext}"
    dept = instance.department or instance.branch or 'General'
    yr = instance.year or '1'
    sec = instance.section or 'A'
    return f'Student_Images/{dept}/{yr}/{sec}/{filename}'

class Student(models.Model):
    BRANCH = (
        ('CSE','CSE'),
        ('IT','IT'),
        ('ECE','ECE'),
        ('CHEM','CHEM'),
        ('MECH','MECH'),
        ('EEE','EEE'),
    )
    YEAR = (
        ('1','1'),
        ('2','2'),
        ('3','3'),
        ('4','4'),
    )
    SECTION = (
        ('A','A'),
        ('B','B'),
        ('C','C'),
    )

    register_number = models.CharField(max_length=200, unique=True, null=True, blank=True)
    registration_id = models.CharField(max_length=200, null=True, blank=True)
    name = models.CharField(max_length=200, null=True, blank=True)
    firstname = models.CharField(max_length=200, null=True, blank=True)
    lastname = models.CharField(max_length=200, null=True, blank=True)
    department = models.CharField(max_length=100, null=True, blank=True)
    branch = models.CharField(max_length=100, null=True, blank=True, choices=BRANCH)
    year = models.CharField(max_length=100, null=True, blank=True, choices=YEAR)
    section = models.CharField(max_length=100, null=True, blank=True, choices=SECTION)
    email = models.EmailField(max_length=200, null=True, blank=True)
    phone = models.CharField(max_length=200, null=True, blank=True)
    profile_pic = models.ImageField(upload_to=student_directory_path, null=True, blank=True)
    enrolled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='enrolled_students')
    enrolled_by_name = models.CharField(max_length=200, null=True, blank=True, default='Institutional Registry')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        def _clean_numeric(v):
            if not v:
                return v
            s = str(v).strip()
            if s.endswith('.0') and s[:-2].replace('-', '').replace('+', '').isdigit():
                return s[:-2]
            return s

        if self.register_number:
            self.register_number = _clean_numeric(self.register_number)
        if self.registration_id:
            self.registration_id = _clean_numeric(self.registration_id)
        if self.phone:
            self.phone = _clean_numeric(self.phone)

        if self.year:
            s_yr = str(self.year).strip().lower()
            if any(k in s_yr for k in ['4th', 'four', 'yr 4', 'year 4', 'iv', '4']):
                self.year = '4'
            elif any(k in s_yr for k in ['3rd', '3 rd', '3_rd', 'third', 'yr 3', 'year 3', 'iii', '3']):
                self.year = '3'
            elif any(k in s_yr for k in ['2nd', '2 nd', 'second', 'yr 2', 'year 2', 'ii', '2']):
                self.year = '2'
            elif any(k in s_yr for k in ['1st', '1 st', 'first', 'yr 1', 'year 1', 'i', '1']):
                self.year = '1'

        if self.register_number and not self.registration_id:
            self.registration_id = self.register_number
        elif self.registration_id and not self.register_number:
            self.register_number = self.registration_id

        if self.department and not self.branch:
            self.branch = self.department
        elif self.branch and not self.department:
            self.department = self.branch

        if self.name and not (self.firstname or self.lastname):
            parts = self.name.strip().split(' ', 1)
            self.firstname = parts[0]
            self.lastname = parts[1] if len(parts) > 1 else ''
        elif (self.firstname or self.lastname) and not self.name:
            self.name = f"{self.firstname or ''} {self.lastname or ''}".strip()

        super().save(*args, **kwargs)

    def get_primary_photo(self):
        primary = self.photos.filter(is_primary=True).first()
        if not primary:
            primary = self.photos.order_by('photo_order', 'id').first()
        return primary

    def get_primary_photo_url(self):
        primary = self.get_primary_photo()
        if primary:
            return f"/students/{self.id}/photo/"
        if self.profile_pic:
            return self.profile_pic.url
        return "/static/images/default_avatar.png"

    def __str__(self):
        return str(self.register_number or self.registration_id or self.name or "Student")


class StudentPhoto(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='photos')
    drive_file_id = models.CharField(max_length=255)
    source_url = models.URLField(max_length=1000, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    photo_order = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ['photo_order', 'id']

    def __str__(self):
        status = "Primary" if self.is_primary else f"Photo #{self.photo_order}"
        return f"{self.student.register_number} - {status} ({self.drive_file_id})"


class FaceEmbedding(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='embeddings')
    embedding = models.TextField(help_text="JSON or text representation of vector embedding")
    photo_reference = models.ForeignKey(StudentPhoto, on_delete=models.SET_NULL, null=True, blank=True)
    model_name = models.CharField(max_length=100, default='default')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return f"Embedding ({self.model_name}) for {self.student.register_number}"


class AttendanceSession(models.Model):
    date = models.DateField(null=True, blank=True)
    department = models.CharField(max_length=100, null=True, blank=True)
    year = models.CharField(max_length=100, null=True, blank=True)
    section = models.CharField(max_length=100, null=True, blank=True)
    subject = models.CharField(max_length=200, null=True, blank=True)
    teacher = models.CharField(max_length=200, null=True, blank=True)
    classroom_image = models.ImageField(upload_to='Classroom_Images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return f"Session {self.date} - {self.department} {self.year}-{self.section}"


class Attendence(models.Model):
    session = models.ForeignKey(AttendanceSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendances')
    student_ref = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendances')
    Faculty_Name = models.CharField(max_length=200, null=True, blank=True)
    Student_ID = models.CharField(max_length=200, null=True, blank=True)
    date = models.DateField(null=True, blank=True)
    time = models.TimeField(auto_now_add=True, null=True)
    branch = models.CharField(max_length=200, null=True)
    year = models.CharField(max_length=200, null=True)
    section = models.CharField(max_length=200, null=True)
    period = models.CharField(max_length=200, null=True)
    status = models.CharField(max_length=200, null=True, default='Absent')
    confidence = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.Student_ID:
            s = str(self.Student_ID).strip()
            if s.endswith('.0') and s[:-2].replace('-', '').replace('+', '').isdigit():
                self.Student_ID = s[:-2]
            else:
                self.Student_ID = s
        super().save(*args, **kwargs)

    def __str__(self):
        return str(str(self.Student_ID) + "_" + str(self.date) + "_" + str(self.period))


from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_faculty_profile(sender, instance, created, **kwargs):
    if created and not kwargs.get('raw', False):
        Faculty.objects.get_or_create(user=instance, defaults={
            'firstname': instance.first_name or instance.username,
            'lastname': instance.last_name or '',
            'email': instance.email or ''
        })