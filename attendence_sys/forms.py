from django.forms import ModelForm

from .models import *

class CreateStudentForm(ModelForm):
    class Meta:
        model = Student
        fields = '__all__'
    def __init__(self, *args, **kwargs):
        super(CreateStudentForm, self).__init__(*args, **kwargs)
        for visible in self.visible_fields():
            visible.field.widget.attrs['class'] = 'form-control'

class FacultyForm(ModelForm):
    class Meta:
        model = Faculty
        fields = '__all__'
        exclude = ['user']
    def __init__(self, *args, **kwargs):
        super(FacultyForm, self).__init__(*args, **kwargs)
        for visible in self.visible_fields():
            visible.field.widget.attrs['class'] = 'form-control'


from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.models import User
from django.db.models import Q

class SmartPasswordResetForm(PasswordResetForm):
    """
    Enhanced PasswordResetForm that matches users by User.email or linked Faculty.email.
    Maintains full anti-fraud protection by not revealing whether an email exists.
    """
    def get_users(self, email):
        email = (email or '').strip()
        if not email:
            return

        active_users = User.objects.filter(
            Q(email__iexact=email) | Q(faculty__email__iexact=email),
            is_active=True
        ).distinct()

        for u in active_users:
            if not u.email and getattr(u, 'faculty', None) and u.faculty.email:
                u.email = u.faculty.email
                u.save(update_fields=['email'])
            if u.has_usable_password():
                yield u
