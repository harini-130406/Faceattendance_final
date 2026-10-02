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


class SmartFailoverEmailBackend(EmailBackend):
    """
    Resilient Email Backend supporting both HTTPS Email APIs (Port 443 - unblocked on Railway)
    and Multi-Strategy SMTP (Ports 465 SSL & 587 STARTTLS).
    
    1. If RESEND_API_KEY is present, dispatches via Resend HTTPS REST API (Port 443).
    2. If BREVO_API_KEY is present, dispatches via Brevo HTTPS REST API (Port 443).
    3. Otherwise, falls back to direct SMTP with IPv4 SNI wrapping and multi-port failover.
    """
    def __init__(self, host=None, port=None, username=None, password=None,
                 use_tls=None, fail_silently=False, use_ssl=None, timeout=None,
                 ssl_keyfile=None, ssl_certfile=None, **kwargs):
        
        # Sanitize password and username (strip all inner and outer whitespace from app passwords)
        default_pwd = getattr(settings, 'EMAIL_HOST_PASSWORD', 'rpwuxrwphizixghb') or 'rpwuxrwphizixghb'
        sanitized_password = (password or default_pwd).replace(' ', '').strip()
        default_usr = getattr(settings, 'EMAIL_HOST_USER', 'proconnect795@gmail.com') or 'proconnect795@gmail.com'
        sanitized_user = (username or default_usr).strip()

        super().__init__(
            host=host or getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
            port=port or getattr(settings, 'EMAIL_PORT', 465),
            username=sanitized_user,
            password=sanitized_password,
            use_tls=use_tls if use_tls is not None else getattr(settings, 'EMAIL_USE_TLS', False),
            fail_silently=fail_silently,
            use_ssl=use_ssl if use_ssl is not None else getattr(settings, 'EMAIL_USE_SSL', True),
            timeout=timeout or getattr(settings, 'EMAIL_TIMEOUT', 15),
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            **kwargs
        )

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        # Check for HTTPS Email API Keys (Bypasses Railway SMTP firewall on Port 443)
        resend_key = os.environ.get('RESEND_API_KEY') or getattr(settings, 'RESEND_API_KEY', '')
        brevo_key = os.environ.get('BREVO_API_KEY') or getattr(settings, 'BREVO_API_KEY', '')

        if resend_key:
            return self._send_via_resend(email_messages, resend_key.strip())
        elif brevo_key:
            return self._send_via_brevo(email_messages, brevo_key.strip())

        # Fallback to standard SMTP
        return super().send_messages(email_messages)

    def _send_via_resend(self, email_messages, api_key):
        num_sent = 0
        url = "https://api.resend.com/emails"
        
        for msg in email_messages:
            try:
                # Extract HTML alternative if present
                html_body = None
                if hasattr(msg, 'alternatives'):
                    for content, mimetype in msg.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                from_sender = msg.from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'proconnect795@gmail.com')
                # If using generic domain, default to Resend verified testing sender
                if "@gmail.com" in from_sender and not os.environ.get('RESEND_CUSTOM_DOMAIN'):
                    from_sender = "Smart Attendance System <onboarding@resend.dev>"

                payload = {
                    "from": from_sender,
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
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "SmartAttendanceSystem/1.0"
                    },
                    method="POST"
                )

                logger.info(f"[SmartFailoverEmail] Dispatching via Resend HTTPS API to {msg.to}...")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    res_body = response.read().decode('utf-8')
                    logger.info(f"[SmartFailoverEmail] Resend API success: {res_body}")
                    num_sent += 1
            except Exception as e:
                logger.error(f"[SmartFailoverEmail] Resend API error: {e}")
                if not self.fail_silently:
                    raise e

        return num_sent

    def _send_via_brevo(self, email_messages, api_key):
        num_sent = 0
        url = "https://api.brevo.com/v3/smtp/email"
        
        for msg in email_messages:
            try:
                html_body = None
                if hasattr(msg, 'alternatives'):
                    for content, mimetype in msg.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                payload = {
                    "sender": {
                        "name": "Smart Attendance System",
                        "email": getattr(settings, 'EMAIL_HOST_USER', 'proconnect795@gmail.com')
                    },
                    "to": [{"email": addr} for addr in msg.to],
                    "subject": msg.subject,
                    "textContent": msg.body or "",
                }
                if html_body:
                    payload["htmlContent"] = html_body

                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "api-key": api_key,
                        "Content-Type": "application/json",
                        "User-Agent": "SmartAttendanceSystem/1.0"
                    },
                    method="POST"
                )

                logger.info(f"[SmartFailoverEmail] Dispatching via Brevo HTTPS API to {msg.to}...")
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    res_body = response.read().decode('utf-8')
                    logger.info(f"[SmartFailoverEmail] Brevo API success: {res_body}")
                    num_sent += 1
            except Exception as e:
                logger.error(f"[SmartFailoverEmail] Brevo API error: {e}")
                if not self.fail_silently:
                    raise e

        return num_sent

    def open(self):
        if self.connection:
            return False

        last_error = None

        # Strategy 1: Standard SMTP_SSL on Port 465
        try:
            logger.info(f"[SmartFailoverEmail] Connecting to {self.host}:465 (SSL)...")
            self.connection = smtplib.SMTP_SSL(self.host, 465, timeout=self.timeout)
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info("[SmartFailoverEmail] Connected & authenticated via Port 465 SSL!")
            return True
        except Exception as e1:
            logger.warning(f"[SmartFailoverEmail] Port 465 SSL failed: {e1}")
            last_error = e1
            self._close_conn()

        # Strategy 2: Explicit IPv4 socket connection with SSL SNI on Port 465
        # (Resolves issues where cloud environments lack IPv6 routing for smtp.gmail.com)
        try:
            logger.info(f"[SmartFailoverEmail] Resolving IPv4 for {self.host}:465...")
            ipv4_addr = socket.gethostbyname(self.host)
            ctx = ssl.create_default_context()
            raw_sock = socket.create_connection((ipv4_addr, 465), timeout=self.timeout)
            ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=self.host)
            self.connection = smtplib.SMTP_SSL()
            self.connection.sock = ssl_sock
            self.connection.file = ssl_sock.makefile('rb')
            self.connection.getreply()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info("[SmartFailoverEmail] Connected & authenticated via IPv4 SNI Port 465 SSL!")
            return True
        except Exception as e2:
            logger.warning(f"[SmartFailoverEmail] IPv4 SNI Port 465 failed: {e2}")
            last_error = e2
            self._close_conn()

        # Strategy 3: STARTTLS on Port 587
        try:
            logger.info(f"[SmartFailoverEmail] Connecting to {self.host}:587 (STARTTLS)...")
            self.connection = smtplib.SMTP(self.host, 587, timeout=self.timeout)
            self.connection.ehlo()
            self.connection.starttls()
            self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info("[SmartFailoverEmail] Connected & authenticated via Port 587 STARTTLS!")
            return True
        except Exception as e3:
            logger.warning(f"[SmartFailoverEmail] Port 587 STARTTLS failed: {e3}")
            last_error = e3
            self._close_conn()

        logger.error(f"[SmartFailoverEmail] All email connection strategies exhausted. Last error: {last_error}")
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
