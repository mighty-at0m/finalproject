from twilio.rest import Client

# Replace these with your actual Twilio credentials
TWILIO_ACCOUNT_SID = 'your_account_sid_here'
TWILIO_AUTH_TOKEN = 'your_auth_token_here'
TWILIO_PHONE_NUMBER = 'your_twilio_number_here'  # e.g. +12345678901

def send_sms(to_phone, message):
    """
    Send SMS to a phone number.
    to_phone: recipient number e.g. +2348012345678
    message: SMS text content
    """
    try:
        # Format Nigerian number properly
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
        print(f"SMS sent to {to_phone}: {msg.sid}")
        return True, msg.sid

    except Exception as e:
        print(f"SMS failed: {str(e)}")
        return False, str(e)


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