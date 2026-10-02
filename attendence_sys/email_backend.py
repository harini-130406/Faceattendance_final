import smtplib
import ssl
import logging
from django.core.mail.backends.smtp import EmailBackend
from django.conf import settings

logger = logging.getLogger(__name__)


class SmartFailoverEmailBackend(EmailBackend):
    """
    Robust SMTP email backend with automatic multi-port failover (587 TLS -> 465 SSL).
    Handles cloud provider port blocking, transient network timeouts, and app password whitespace.
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
            port=port or getattr(settings, 'EMAIL_PORT', 587),
            username=sanitized_user,
            password=sanitized_password,
            use_tls=use_tls if use_tls is not None else getattr(settings, 'EMAIL_USE_TLS', True),
            fail_silently=fail_silently,
            use_ssl=use_ssl if use_ssl is not None else getattr(settings, 'EMAIL_USE_SSL', False),
            timeout=timeout or getattr(settings, 'EMAIL_TIMEOUT', 5),
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            **kwargs
        )

    def open(self):
        if self.connection:
            return False

        # Attempt 1: Standard connection (typically Port 587 STARTTLS)
        try:
            logger.info(f"[SmartFailoverEmail] Connecting to {self.host}:{self.port} (TLS={self.use_tls}, SSL={self.use_ssl})...")
            if self.use_ssl:
                self.connection = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                self.connection = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                if self.use_tls:
                    self.connection.ehlo()
                    self.connection.starttls()
                    self.connection.ehlo()

            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info(f"[SmartFailoverEmail] Connected and authenticated on primary port {self.port}!")
            return True
        except Exception as e1:
            logger.warning(f"[SmartFailoverEmail] Primary port {self.port} failed ({e1}). Attempting Port 465 SSL failover...")

        # Attempt 2: Direct SSL on Port 465
        try:
            self.connection = smtplib.SMTP_SSL(self.host, 465, timeout=self.timeout)
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info("[SmartFailoverEmail] Successfully connected and authenticated on failover Port 465 SSL!")
            return True
        except Exception as e2:
            logger.warning(f"[SmartFailoverEmail] Failover Port 465 failed ({e2}). Attempting Port 587 TLS failover...")

        # Attempt 3: STARTTLS on Port 587
        try:
            self.connection = smtplib.SMTP(self.host, 587, timeout=self.timeout)
            self.connection.ehlo()
            self.connection.starttls()
            self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            logger.info("[SmartFailoverEmail] Successfully connected and authenticated on failover Port 587 TLS!")
            return True
        except Exception as e3:
            logger.error(f"[SmartFailoverEmail] All SMTP attempts failed. Port {self.port}: {e1}, Port 465: {e2}, Port 587: {e3}")
            if not self.fail_silently:
                raise e3
            return False
