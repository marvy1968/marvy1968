import os, datetime, pytz, requests
from dotenv import load_dotenv

load_dotenv('/home/moonrider1968/bots/killer_beast_sniper/.env')
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
BASE_URL = os.getenv('APCA_API_BASE_URL', 'https://paper-api.alpaca.markets')

def check_and_flatten():
    eastern = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(eastern)
    
    # Run only at or after 3:50 PM EDT on weekdays
    if now.weekday() < 5 and (now.hour == 15 and now.minute >= 50):
        print(f"[{now}] 🚨 EOD WINDOW ACTIVE (3:50 PM EDT) - Closing all intraday open positions...")
        headers = {
            'APCA-API-KEY-ID': API_KEY,
            'APCA-API-SECRET-KEY': SECRET_KEY
        }
        res = requests.delete(f"{BASE_URL}/v2/positions", headers=headers)
        print("Positions Liquidation Response:", res.status_code, res.text)
    else:
        print(f"[{now}] Outside EOD liquidation window.")

if __name__ == '__main__':
    check_and_flatten()
