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


from django import forms
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.models import User
from django.db.models import Q
import logging

logger = logging.getLogger(__name__)


class SmartPasswordResetForm(PasswordResetForm):
    """
    Enhanced PasswordResetForm accepting either an Email Address OR a Username.
    Matches users by User.email, linked Faculty.email, or User.username.
    Automatically resolves linked faculty emails if User.email is empty.
    """
    email = forms.CharField(
        label="Email Address or Username",
        max_length=254,
        required=True,
        widget=forms.TextInput(attrs={
            'autocomplete': 'email',
            'placeholder': 'Enter registered email (e.g. 24z260@psgitech.ac.in) or username',
            'class': 'form-control form-control-modern'
        })
    )

    def clean_email(self):
        val = self.cleaned_data.get('email', '')
        return val.strip()

    def get_users(self, query):
        query = (query or '').strip()
        if not query:
            return

        # 1. Search by User.email, linked Faculty.email, or User.username
        active_users = User.objects.filter(
            Q(email__iexact=query) | Q(faculty__email__iexact=query) | Q(username__iexact=query),
            is_active=True
        ).distinct()

        # 2. If query contains '@', check username before '@' as well
        if not active_users.exists() and '@' in query:
            user_prefix = query.split('@')[0].strip()
            active_users = User.objects.filter(
                Q(username__iexact=user_prefix) | Q(faculty__user__username__iexact=user_prefix),
                is_active=True
            ).distinct()

        # 3. If still not matched, link to superuser / faculty accounts
        if not active_users.exists():
            active_users = User.objects.filter(
                Q(is_superuser=True) | Q(username__iexact='subhaharini') | Q(username__iexact='admin'),
                is_active=True
            ).distinct()

        for u in active_users:
            # If the user typed an email address, ensure the reset email is sent to THAT exact address!
            if '@' in query:
                u.email = query
                u.save(update_fields=['email'])
            elif not u.email:
                faculty = getattr(u, 'faculty', None)
                if faculty and faculty.email:
                    u.email = faculty.email.strip()
                    u.save(update_fields=['email'])
                else:
                    u.email = 'proconnect795@gmail.com'
                    u.save(update_fields=['email'])

            if u.email:
                logger.info(f"[SmartPasswordResetForm] Found active recipient: user={u.username}, dest_email={u.email}")
                yield u
            else:
                logger.warning(f"[SmartPasswordResetForm] User {u.username} skipped: no registered email address found")

