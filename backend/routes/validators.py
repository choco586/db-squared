# backend/routes/validators.py
#
# The full Validator class now lives in db.py so it's available to all
# route files without circular imports.
#
# This file is kept for backward compatibility in case any existing code
# imports QuickValidator from here. New code should import directly from db:
#
#   from db import Validator, ValidationError
#

from db import Validator, ValidationError


class QuickValidator:
    """
    Legacy validator — kept for backward compatibility.
    All new code should use db.Validator directly.
    """

    @staticmethod
    def sanitize_sql_input(text, max_len=255):
        return Validator.sanitize(text, max_len)

    @staticmethod
    def validate_user_input(data):
        try:
            name  = Validator.require_string(data.get('name', ''), 'name', min_len=2, max_len=100)
            email = Validator.validate_username(data.get('email', ''))
            return True, {'name': name, 'email': email}
        except ValidationError as e:
            return False, str(e)

    @staticmethod
    def validate_user_id(user_id):
        try:
            uid = Validator.require_positive_int(user_id, 'user_id')
            return True, uid
        except ValidationError as e:
            return False, str(e)
