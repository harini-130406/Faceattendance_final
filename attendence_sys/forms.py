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
        val = self.cleaned_data.get('email', '').strip()
        users = list(self.get_users(val))
        if not users:
            raise forms.ValidationError(
                "No registered account found with that email address or username. Please check your spelling or create an account."
            )
        return val

    def get_users(self, query):
        query = (query or '').strip()
        if not query:
            return

        # 1. Match active accounts by User.email, linked Faculty.email, or User.username
        active_users = User.objects.filter(
            Q(email__iexact=query) | Q(faculty__email__iexact=query) | Q(username__iexact=query),
            is_active=True
        ).distinct()

        # 2. If user entered an email like 24z260@psgitech.ac.in, also search username matching the prefix
        if not active_users.exists() and '@' in query:
            user_prefix = query.split('@')[0].strip()
            active_users = User.objects.filter(
                Q(username__iexact=user_prefix) | Q(faculty__user__username__iexact=user_prefix),
                is_active=True
            ).distinct()

        for u in active_users:
            if '@' in query:
                # Ensure reset email is sent to the exact address requested by the user
                if not u.email or u.email.lower() != query.lower():
                    u.email = query
                    u.save(update_fields=['email'])
            elif not u.email:
                faculty = getattr(u, 'faculty', None)
                if faculty and faculty.email:
                    u.email = faculty.email.strip()
                    u.save(update_fields=['email'])

            if u.email:
                yield u

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        """
        Send a django.core.mail.EmailMultiAlternatives to `to_email`.
        Enforces fail_silently=False so delivery failures are never swallowed.
        """
        from django.template import loader
        from django.core.mail import EmailMultiAlternatives
        from django.conf import settings

        subject = loader.render_to_string(subject_template_name, context)
        # Email subject *must not* contain newlines
        subject = "".join(subject.splitlines())
        body = loader.render_to_string(email_template_name, context)

        sender = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'proconnect795@gmail.com')

        email_message = EmailMultiAlternatives(subject, body, sender, [to_email])
        if html_email_template_name is not None:
            html_email = loader.render_to_string(html_email_template_name, context)
            email_message.attach_alternative(html_email, "text/html")

        logger.info(f"[SmartPasswordResetForm] Sending password reset email from {sender} to {to_email}...")
        email_message.send(fail_silently=False)
        logger.info(f"[SmartPasswordResetForm] Successfully dispatched password reset email to {to_email}")


