from app import send_otp_email, app
import sys

def test_email():
    print("Testing email sending...")
    # Using a dummy email or the user's email if evident
    target = "ezaan.amin@gmail.com" 
    
    with app.app_context():
        try:
            success = send_otp_email(target, "123456")
            if success:
                print("✅ Email sent successfully.")
            else:
                print("❌ Email failed (returned False).")
        except Exception as e:
            print(f"❌ Email function raised exception: {e}")

if __name__ == "__main__":
    test_email()
