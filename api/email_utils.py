from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


def send_otp_email(user, otp_code):
    """
    Send OTP email to user for password reset.
    
    Args:
        user: Django User object
        otp_code: 6-digit OTP code string
        
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        subject = '[SZLG Igazoláskezelő] Elfelejtett jelszó'
        
        # Render HTML email template
        html_message = render_to_string('emails/otp_reset_password.html', {
            'user': user,
            'otp_code': otp_code[:3] + '-' + otp_code[3:],
            'current_year': timezone.now().year,
            'timestamp': timezone.now(),
        })
        
        # Create plain text version
        plain_message = strip_tags(html_message)
        
        # Send email
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=False,
        )
        
        logger.info(f"OTP email sent successfully to {user.email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send OTP email to {user.email}: {str(e)}")
        return False


def send_password_changed_notification(user):
    """
    Send notification email when password is successfully changed.
    
    Args:
        user: Django User object
        
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        subject = 'Jelszó sikeresen megváltoztatva'
        
        message = f"""
Kedves {user.get_full_name() or user.username}!

A jelszava sikeresen megváltoztatásra került.

Ha nem Ön változtatta meg a jelszót, kérjük azonnal vegye fel a kapcsolatot 
a rendszer adminisztrátorával.

Időpont: {timezone.now().strftime('%Y. %m. %d. %H:%M')}

Üdvözlettel,
Igazoláskezelő Rendszer
        """
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        
        logger.info(f"Password change notification sent to {user.email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send password change notification to {user.email}: {str(e)}")
        return False