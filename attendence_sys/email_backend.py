import os
import json
import smtplib
import ssl
import socket
import logging
import urllib.request
import urllib.error
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings

logger = logging.getLogger(__name__)


def _extract_name_email(addr_string):
    """Parse 'Display Name <email@example.com>' or plain 'email@example.com'."""
    addr_string = (addr_string or '').strip()
    if '<' in addr_string and '>' in addr_string:
        name = addr_string.split('<')[0].strip()
        email = addr_string.split('<')[1].replace('>', '').strip()
    else:
        name = ''
        email = addr_string
    return name, email


class SmartFailoverEmailBackend(EmailBackend):
    """
    Resilient Email Backend with priority order:
    1. SendGrid HTTPS API  (SENDGRID_API_KEY)  - Most reliable, free 100/day
    2. Brevo HTTPS API     (BREVO_API_KEY)
    3. Resend HTTPS API    (RESEND_API_KEY)
    4. Direct SMTP fallback (Gmail / any SMTP)
    All API methods use Port 443 HTTPS - never blocked by Railway cloud firewall.
    """

    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 ssl_keyfile=None, ssl_certfile=None, **kwargs):

        default_pwd = getattr(settings, 'EMAIL_HOST_PASSWORD', '') or ''
        sanitized_password = (password or default_pwd).replace(' ', '').strip()
        default_usr = getattr(settings, 'EMAIL_HOST_USER', '') or ''
        sanitized_user = (username or default_usr).strip()

        super().__init__(
            host=host or getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
            port=port or getattr(settings, 'EMAIL_PORT', 587),
            username=sanitized_user,
            password=sanitized_password,
            use_tls=use_tls if use_tls is not None else getattr(settings, 'EMAIL_USE_TLS', True),
            fail_silently=fail_silently,
            use_ssl=use_ssl if use_ssl is not None else getattr(settings, 'EMAIL_USE_SSL', False),
            timeout=timeout or getattr(settings, 'EMAIL_TIMEOUT', 15),
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            **kwargs
        )

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        sendgrid_key = (os.environ.get('SENDGRID_API_KEY') or getattr(settings, 'SENDGRID_API_KEY', '')).strip()
        brevo_key = (os.environ.get('BREVO_API_KEY') or getattr(settings, 'BREVO_API_KEY', '')).strip()
        resend_key = (os.environ.get('RESEND_API_KEY') or getattr(settings, 'RESEND_API_KEY', '')).strip()

        if sendgrid_key:
            logger.info("[SmartFailoverEmail] Using SendGrid HTTPS API")
            return self._send_via_sendgrid(email_messages, sendgrid_key)
        elif brevo_key:
            logger.info("[SmartFailoverEmail] Using Brevo HTTPS API")
            return self._send_via_brevo(email_messages, brevo_key)
        elif resend_key:
            logger.info("[SmartFailoverEmail] Using Resend HTTPS API")
            return self._send_via_resend(email_messages, resend_key)

        logger.info("[SmartFailoverEmail] Using SMTP fallback")
        return super().send_messages(email_messages)

    # ──────────────────────────────────────────────────────────────────────────
    # SendGrid HTTPS API
    # ──────────────────────────────────────────────────────────────────────────
    def _send_via_sendgrid(self, email_messages, api_key):
        num_sent = 0
        url = "https://api.sendgrid.com/v3/mail/send"

        default_from_name, default_from_email = _extract_name_email(
            os.environ.get('SENDGRID_FROM_EMAIL')
            or getattr(settings, 'SENDGRID_FROM_EMAIL', '')
            or getattr(settings, 'DEFAULT_FROM_EMAIL', '')
            or os.environ.get('EMAIL_HOST_USER', '')
        )
        if not default_from_name:
            default_from_name = (
                os.environ.get('SENDGRID_FROM_NAME')
                or getattr(settings, 'SENDGRID_FROM_NAME', '')
                or "Smart FaceAttendance"
            )

        for msg in email_messages:
            try:
                html_body = None
                if hasattr(msg, 'alternatives'):
                    for content, mimetype in msg.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                from_name, from_email = _extract_name_email(msg.from_email)
                if not from_name:
                    from_name = default_from_name
                if not from_email or '@' not in from_email:
                    from_email = default_from_email

                payload = {
                    "from": {"email": from_email, "name": from_name},
                    "personalizations": [
                        {
                            "to": [{"email": addr.strip()} for addr in msg.to if addr.strip()],
                            "subject": msg.subject,
                        }
                    ],
                    "content": [{"type": "text/plain", "value": msg.body or " "}],
                }
                if html_body:
                    payload["content"].append({"type": "text/html", "value": html_body})

                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST"
                )

                logger.info(f"[SmartFailoverEmail] SendGrid: {from_email} -> {msg.to}")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    logger.info(f"[SmartFailoverEmail] SendGrid success: HTTP {response.status}")
                    num_sent += 1

            except urllib.error.HTTPError as http_err:
                try:
                    err_body = http_err.read().decode('utf-8')
                    err_json = json.loads(err_body)
                    errs = err_json.get('errors', [])
                    err_detail = '; '.join(e.get('message', '') for e in errs) or http_err.reason
                except Exception:
                    err_detail = str(http_err)
                logger.error(f"[SmartFailoverEmail] SendGrid rejected: {err_detail}")
                if not self.fail_silently:
                    raise Exception(f"SendGrid error: {err_detail}")
            except Exception as e:
                logger.error(f"[SmartFailoverEmail] SendGrid error: {e}")
                if not self.fail_silently:
                    raise

        return num_sent

    # ──────────────────────────────────────────────────────────────────────────
    # Brevo HTTPS API
    # ──────────────────────────────────────────────────────────────────────────
    def _send_via_brevo(self, email_messages, api_key):
        num_sent = 0
        url = "https://api.brevo.com/v3/smtp/email"

        default_from_raw = (
            os.environ.get('BREVO_SENDER_EMAIL')
            or getattr(settings, 'BREVO_SENDER_EMAIL', '')
            or getattr(settings, 'DEFAULT_FROM_EMAIL', '')
            or os.environ.get('EMAIL_HOST_USER', '')
        )
        default_from_name, default_from_email = _extract_name_email(default_from_raw)
        if not default_from_name:
            default_from_name = (
                os.environ.get('BREVO_SENDER_NAME')
                or getattr(settings, 'BREVO_SENDER_NAME', '')
                or "Smart FaceAttendance"
            )

        for msg in email_messages:
            try:
                html_body = None
                if hasattr(msg, 'alternatives'):
                    for content, mimetype in msg.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                from_name, from_email = _extract_name_email(msg.from_email)
                if not from_name:
                    from_name = default_from_name
                if not from_email or '@' not in from_email:
                    from_email = default_from_email

                payload = {
                    "sender": {"name": from_name, "email": from_email},
                    "to": [{"email": addr.strip()} for addr in msg.to if addr.strip()],
                    "subject": msg.subject,
                    "textContent": msg.body or "",
                }
                if html_body:
                    payload["htmlContent"] = html_body

                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"api-key": api_key, "Content-Type": "application/json"},
                    method="POST"
                )

                logger.info(f"[SmartFailoverEmail] Brevo: {from_email} -> {msg.to}")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    logger.info(f"[SmartFailoverEmail] Brevo success: {response.read().decode()}")
                    num_sent += 1

            except urllib.error.HTTPError as http_err:
                try:
                    err_json = json.loads(http_err.read().decode('utf-8'))
                    err_detail = err_json.get('message', http_err.reason)
                except Exception:
                    err_detail = str(http_err)
                logger.error(f"[SmartFailoverEmail] Brevo rejected: {err_detail}")
                if not self.fail_silently:
                    raise Exception(f"Brevo error: {err_detail}")
            except Exception as e:
                logger.error(f"[SmartFailoverEmail] Brevo error: {e}")
                if not self.fail_silently:
                    raise

        return num_sent

    # ──────────────────────────────────────────────────────────────────────────
    # Resend HTTPS API
    # ──────────────────────────────────────────────────────────────────────────
    def _send_via_resend(self, email_messages, api_key):
        num_sent = 0
        url = "https://api.resend.com/emails"

        for msg in email_messages:
            try:
                html_body = None
                if hasattr(msg, 'alternatives'):
                    for content, mimetype in msg.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                from_name, from_email = _extract_name_email(msg.from_email)
                sender_str = f"{from_name} <{from_email}>" if from_name else from_email

                payload = {
                    "from": sender_str,
                    "to": list(msg.to),
                    "subject": msg.subject,
                    "text": msg.body or "",
                }
                if html_body:
                    payload["html"] = html_body

                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    method="POST"
                )

                logger.info(f"[SmartFailoverEmail] Resend: {sender_str} -> {msg.to}")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    logger.info(f"[SmartFailoverEmail] Resend success: {response.read().decode()}")
                    num_sent += 1

            except urllib.error.HTTPError as http_err:
                try:
                    err_json = json.loads(http_err.read().decode('utf-8'))
                    err_detail = err_json.get('message', http_err.reason)
                except Exception:
                    err_detail = str(http_err)
                logger.error(f"[SmartFailoverEmail] Resend rejected: {err_detail}")
                if not self.fail_silently:
                    raise Exception(f"Resend error: {err_detail}")
            except Exception as e:
                logger.error(f"[SmartFailoverEmail] Resend error: {e}")
                if not self.fail_silently:
                    raise

        return num_sent

    # ──────────────────────────────────────────────────────────────────────────
    # SMTP Fallback (multi-strategy)
    # ──────────────────────────────────────────────────────────────────────────
    def open(self):
        if self.connection:
            return False

        last_error = None

        for attempt, strategy in enumerate(['SMTP_SSL:465', 'IPv4_SNI:465', 'STARTTLS:587']):
            try:
                if strategy == 'SMTP_SSL:465':
                    logger.info(f"[SmartFailoverEmail] Trying Port 465 SSL...")
                    self.connection = smtplib.SMTP_SSL(self.host, 465, timeout=self.timeout)
                elif strategy == 'IPv4_SNI:465':
                    logger.info(f"[SmartFailoverEmail] Trying IPv4 SNI Port 465...")
                    ipv4_addr = socket.gethostbyname(self.host)
                    ctx = ssl.create_default_context()
                    raw_sock = socket.create_connection((ipv4_addr, 465), timeout=self.timeout)
                    ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
                    self.connection = smtplib.SMTP_SSL()
                    self.connection.sock = ssl_sock
                    self.connection.file = ssl_sock.makefile('rb')
                    self.connection.getreply()
                else:
                    logger.info(f"[SmartFailoverEmail] Trying Port 587 STARTTLS...")
                    self.connection = smtplib.SMTP(self.host, 587, timeout=self.timeout)
                    self.connection.ehlo()
                    self.connection.starttls()
                    self.connection.ehlo()

                if self.username and self.password:
                    self.connection.login(self.username, self.password)
                elif self.username and not self.password:
                    raise smtplib.SMTPAuthenticationError(535, "EMAIL_HOST_PASSWORD is not set in environment or .env file.")
                logger.info(f"[SmartFailoverEmail] Connected via {strategy}!")
                return True

            except Exception as err:
                logger.warning(f"[SmartFailoverEmail] {strategy} failed: {err}")
                last_error = err
                self._close_conn()

        logger.error(f"[SmartFailoverEmail] All SMTP strategies failed. Last: {last_error}")
        if not self.fail_silently:
            raise last_error
        return False

    def _close_conn(self):
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None
