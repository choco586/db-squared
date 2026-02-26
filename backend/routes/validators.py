"""
Minimal validators - just enough to prevent SQL injection and basic errors.
"""

class QuickValidator:
    
    @staticmethod
    def sanitize_sql_input(text, max_len=255):
        """Prevent SQL injection"""
        if not text or not isinstance(text, str):
            return ""
        
        # Remove dangerous SQL characters
        dangerous = ["'", '"', ';', '--', '/*', '*/']
        cleaned = text
        for char in dangerous:
            cleaned = cleaned.replace(char, '')
        
        cleaned = cleaned.strip()
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len]
        
        return cleaned
    
    @staticmethod
    def validate_user_input(data):
        """Basic validation for user CRUD operations"""
        # Sanitize first
        name = QuickValidator.sanitize_sql_input(data.get('name', ''), 100)
        email = QuickValidator.sanitize_sql_input(data.get('email', ''), 255)
        
        if not name or len(name) < 2:
            return False, "Name must be at least 2 characters"
        
        if not email or '@' not in email or '.' not in email:
            return False, "Invalid email"
        
        return True, {"name": name, "email": email.lower()}
    
    @staticmethod
    def validate_user_id(user_id):
        """Validate user ID format"""
        try:
            uid = int(user_id)
            if uid <= 0:
                return False, "Invalid user ID"
            return True, uid
        except:
            return False, "User ID must be a number"