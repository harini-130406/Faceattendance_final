from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = "Safely verifies SMTP email sending capability without exposing credentials."

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

        host = getattr(settings, 'EMAIL_HOST', '')
        port = getattr(settings, 'EMAIL_PORT', 587)
        user = getattr(settings, 'EMAIL_HOST_USER', '')
        password = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or user

        if not host or not user or not password:
            raise CommandError(
                "SMTP configuration is incomplete. Please ensure EMAIL_HOST, "
                "EMAIL_HOST_USER, and EMAIL_HOST_PASSWORD environment variables are set."
            )

        subject = "Smart Attendance System - SMTP Verification"
        body = (
            "Hello,\n\n"
            "This is a verification test email from the Smart Attendance System.\n"
            "Your SMTP configuration is working properly.\n\n"
            f"Sender: {from_email}\n"
            f"Host: {host}:{port}\n"
        )

        self.stdout.write(f"Testing SMTP delivery via {host}:{port} to {recipient}...")

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
                raise CommandError("SMTP server accepted connection but did not dispatch message.")
        except Exception as e:
            # Strip any credentials if they accidentally appear in exception messages
            err_msg = str(e)
            if password and password in err_msg:
                err_msg = err_msg.replace(password, "[REDACTED]")
            raise CommandError(f"Email delivery failed: {err_msg}")
