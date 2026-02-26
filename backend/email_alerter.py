# email_alerter.py
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
from datetime import datetime
import os
from dotenv import load_dotenv
load_dotenv()


# Email configuration - YOU'LL UPDATE THESE
EMAIL_HOST = 'smtp.gmaicd cl.com'
EMAIL_PORT = 587
EMAIL_USERNAME = 'karammari04@gmail.com'
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_FROM = 'karammari04@gmail.com'
EMAIL_TO = 'karammari04@gmail.com'

class EmailAlerter:
    def __init__(self):
        self.last_email_time = 0
        self.min_interval = 60  # Don't send more than 1 email per minute
        
    def send_alert(self, subject, body, priority='normal'):
        """Send email in background thread"""
        
        # Rate limiting
        now = time.time()
        if now - self.last_email_time < self.min_interval:
            print(f"⏱️ Email rate limited: {subject}")
            return
        
        def send_task():
            try:
                msg = MIMEMultipart()
                msg['From'] = EMAIL_FROM
                msg['To'] = EMAIL_TO
                msg['Subject'] = f"[DB-Squared] {subject}"
                
                full_body = f"""
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

{body}

---
This is an automated alert from your failover system.
                """
                
                msg.attach(MIMEText(full_body, 'plain'))
                
                server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
                server.starttls()
                server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
                server.send_message(msg)
                server.quit()
                
                self.last_email_time = time.time()
                print(f"📧 Email sent: {subject}")
                
            except Exception as e:
                print(f"❌ Email failed: {e}")
        
        thread = threading.Thread(target=send_task)
        thread.daemon = True
        thread.start()

# Create global instance
email_alerter = EmailAlerter()