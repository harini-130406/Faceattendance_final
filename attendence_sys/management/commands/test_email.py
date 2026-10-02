import os
from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = "Safely verifies email sending capability (Brevo HTTPS API or standard SMTP) without exposing credentials."

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient_email',
            type=str,
            help="Recipient email address to send test message to."
        )

    def handle(self, *args, **options):
        recipient = options.get('recipient_email', '').strip()
        if not recipient or '@' not in recipient:
            raise CommandError("Please provide a valid recipient email address.")

        brevo_key = os.environ.get('BREVO_API_KEY') or getattr(settings, 'BREVO_API_KEY', '')
        resend_key = os.environ.get('RESEND_API_KEY') or getattr(settings, 'RESEND_API_KEY', '')
        host = getattr(settings, 'EMAIL_HOST', '')
        port = getattr(settings, 'EMAIL_PORT', 587)
        user = getattr(settings, 'EMAIL_HOST_USER', '')
        password = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
        from_email = (
            os.environ.get('BREVO_SENDER_EMAIL')
            or getattr(settings, 'BREVO_SENDER_EMAIL', '')
            or getattr(settings, 'DEFAULT_FROM_EMAIL', '')
            or user
            or 'proconnect795@gmail.com'
        )

        has_api = bool(brevo_key or resend_key)
        has_smtp = bool(host and user and password)

        if not has_api and not has_smtp:
            raise CommandError(
                "Email configuration is incomplete. Please set either BREVO_API_KEY (recommended for Railway) "
                "or SMTP environment variables (EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)."
            )

        if brevo_key:
            mode_desc = "Brevo HTTPS API (Port 443 - Railway unblocked)"
        elif resend_key:
            mode_desc = "Resend HTTPS API (Port 443)"
        else:
            mode_desc = f"SMTP ({host}:{port})"

        subject = "Smart Attendance System - Email Verification"
        body = (
            "Hello,\n\n"
            "This is a verification test email from the Smart Attendance System.\n"
            f"Delivery Mode: {mode_desc}\n"
            f"Sender: {from_email}\n\n"
            "Your email configuration is working properly!\n"
        )

        self.stdout.write(f"Testing email delivery via {mode_desc} to {recipient}...")

        try:
            sent = send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=[recipient],
                fail_silently=False
            )
            if sent:
                self.stdout.write(
                    self.style.SUCCESS(f"SUCCESS: Test email successfully dispatched to {recipient}.")
                )
            else:
                raise CommandError("Email service accepted connection but reported 0 messages dispatched.")
        except Exception as e:
            # Strip any credentials if they accidentally appear in exception messages
            err_msg = str(e)
            if password and password in err_msg:
                err_msg = err_msg.replace(password, "[REDACTED]")
            if brevo_key and brevo_key in err_msg:
                err_msg = err_msg.replace(brevo_key, "[REDACTED]")
            if resend_key and resend_key in err_msg:
                err_msg = err_msg.replace(resend_key, "[REDACTED]")
            raise CommandError(f"Email delivery failed: {err_msg}")

