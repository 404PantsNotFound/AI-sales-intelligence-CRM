from email_validator import EmailNotValidError, validate_email


def verify_email(email: str) -> bool:
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False