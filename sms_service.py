import os
import logging
from twilio.rest import Client

logger = logging.getLogger('smart_attendance')

# Load from environment (never hardcode)
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')

def validate_twilio_config():
    """Validate Twilio config and return (ok, detail)."""
    if not TWILIO_ACCOUNT_SID:
        return False, "Twilio not configured: missing TWILIO_ACCOUNT_SID"
    if not TWILIO_AUTH_TOKEN:
        return False, "Twilio not configured: missing TWILIO_AUTH_TOKEN"
    if not TWILIO_PHONE_NUMBER:
        return False, "Twilio not configured: missing TWILIO_PHONE_NUMBER"
    return True, "Configured"

def send_sms(to_phone, message):
    """Send SMS – returns (success, message_or_sid)"""
    ok, detail = validate_twilio_config()
    if not ok:
        logger.warning(detail)
        return False, detail
    try:
        if to_phone.startswith('0'):
            to_phone = '+234' + to_phone[1:]
        elif not to_phone.startswith('+'):
            to_phone = '+234' + to_phone

        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        msg = client.messages.create(
            body=message,
            from_=TWILIO_PHONE_NUMBER,
            to=to_phone
        )
        logger.info(f"SMS sent to {to_phone}: {msg.sid}")
        return True, msg.sid
    except Exception as e:
        raw = str(e)
        if '20003' in raw or 'Authenticate' in raw:
            friendly = 'Twilio authentication failed (error 20003). Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.'
        elif '21211' in raw:
            friendly = 'Invalid destination phone number format (error 21211).'
        elif '21606' in raw:
            friendly = 'The Twilio number cannot send to this destination (error 21606).'
        else:
            friendly = f"Twilio send failed: {raw}"
        logger.error(f"SMS failed: {friendly}")
        return False, friendly

def send_attendance_alert(student_name, parent_phone, course, status, timestamp):
    """Send attendance notification to parent."""
    if status == 'present':
        message = (
            f"ATTENDANCE ALERT\n"
            f"Dear Parent, {student_name} has successfully "
            f"marked attendance for {course} on "
            f"{timestamp.strftime('%d %b %Y at %I:%M %p')}.\n"
            f"- Smart Attendance System, AEFUNAi"
        )
    else:
        message = (
            f"ABSENCE ALERT\n"
            f"Dear Parent, {student_name} was ABSENT for "
            f"{course} on {timestamp.strftime('%d %b %Y at %I:%M %p')}.\n"
            f"Please follow up with your ward.\n"
            f"- Smart Attendance System, AEFUNAi"
        )
    return send_sms(parent_phone, message)

def send_absence_warning(student_name, parent_phone, course, absent_count, total):
    """Send warning when attendance rate drops below 75%."""
    rate = round((total - absent_count) / total * 100) if total > 0 else 0
    message = (
        f"ATTENDANCE WARNING\n"
        f"Dear Parent, {student_name}'s attendance for "
        f"{course} has dropped to {rate}% "
        f"({absent_count} absences out of {total} classes).\n"
        f"Immediate attention required.\n"
        f"- Smart Attendance System, AEFUNAi"
    )
    return send_sms(parent_phone, message)