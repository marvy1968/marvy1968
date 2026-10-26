import os

file_path = 'telegram_sender.py'
try:
    with open(file_path, 'r') as f:
        code = f.read()

    # Replace the hard crash with a safe bypass
    target = "resp.raise_for_status()"
    shield = """if resp.status_code == 429:
            print("🛡️ 429 Rate Limit Hit. Shield active, sleeping 60s...")
            import time; time.sleep(60)
        else:
            resp.raise_for_status()"""
            
    if target in code and "429 Rate Limit Hit" not in code:
        safe_code = code.replace(target, shield)
        with open(file_path, 'w') as f:
            f.write(safe_code)
        print("✅ Telegram 429 Shield Applied Successfully.")
    else:
        print("⚠️ Shield already applied or target line not found.")
except Exception as e:
    print(f"Error applying shield: {e}")
