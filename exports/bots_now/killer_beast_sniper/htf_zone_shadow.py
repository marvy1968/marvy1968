import os, sys, time, logging
from pathlib import Path
from dotenv import load_dotenv

# Set explicit paths and ensure logs directory exists
BASE_DIR = Path('/home/moonrider1968/bots/killer_beast_sniper')
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / '.env')
sys.path.insert(0, str(BASE_DIR))

# Configure logging
logging.basicConfig(
    filename=str(LOGS_DIR / 'htf_zone_shadow.log'),
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

from sector_heat_gauge import get_sector_rankings
from news_catalyst_filter import check_stock_news_catalyst

def evaluate_macro_bias():
    hot_sectors, cold_sectors = get_sector_rankings()
    hot_etfs = [s['etf'] for s in hot_sectors]
    cold_etfs = [s['etf'] for s in cold_sectors]
    
    logging.info(f"🏛️ [HTF MACRO SCAN] Hot Sectors: {hot_etfs} | Cold Sectors: {cold_etfs}")
    print(f"🏛️ [HTF MACRO SCAN] Hot: {hot_etfs} | Cold: {cold_etfs}")
    return hot_etfs, cold_etfs

if __name__ == '__main__':
    logging.info("🚀 HTF Macro Trend & Zone Shadow Engine Initialized")
    print("🚀 HTF Macro Trend & Zone Shadow Engine Initialized")
    while True:
        try:
            evaluate_macro_bias()
            time.sleep(900)  # Scan every 15 minutes
        except Exception as e:
            logging.error(f"Error in HTF Shadow Engine: {e}")
            print(f"Error: {e}")
            time.sleep(60)
