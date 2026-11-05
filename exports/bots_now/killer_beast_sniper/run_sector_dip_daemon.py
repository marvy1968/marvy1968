#!/usr/bin/env python3
import time
import logging
from datetime import datetime, timezone, timedelta
from sector_dip_master import SectorNewsDipEngine, SECTOR_UNIVERSE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sector_dip_daemon.log"),
        logging.StreamHandler()
    ]
)

# EST Timezone Offset (UTC-4)
EST_OFFSET = timedelta(hours=-4)

def is_market_open():
    now_utc = datetime.now(timezone.utc)
    now_est = now_utc + EST_OFFSET
    
    # Check Weekend (5 = Saturday, 6 = Sunday)
    if now_est.weekday() >= 5:
        return False, "Weekend"
    
    # Market Hours: 9:30 AM to 4:00 PM EST
    market_start = now_est.replace(hour=9, minute=30, second=0, microsecond=0)
    market_end   = now_est.replace(hour=16, minute=0, second=0, microsecond=0)

    if market_start <= now_est <= market_end:
        return True, "Open"
    elif now_est < market_start:
        return False, f"Pre-market (opens at 09:30 AM EST, currently {now_est.strftime('%H:%M EST')})"
    else:
        return False, f"Post-market (closed at 16:00 PM EST, currently {now_est.strftime('%H:%M EST')})"

def main():
    logging.info("🚀 STARTING CONTINUOUS SECTOR DIP REBOUND DAEMON...")
    engine = SectorNewsDipEngine(risk_pct=0.01)

    while True:
        try:
            market_active, status_reason = is_market_open()
            
            if market_active:
                logging.info("==========================================================")
                logging.info("   🔍 MARKET OPEN: SCANNING 110 SECTOR LEADERS FOR DIPS   ")
                logging.info("==========================================================")
                
                total_found = 0
                for sector, tickers in SECTOR_UNIVERSE.items():
                    for sym in tickers:
                        setup = engine.scan_ticker(sym, sector)
                        if setup:
                            total_found += 1
                            engine.execute_order(setup)
                
                if total_found == 0:
                    logging.info("ℹ️ Scan complete. No multi-factor setups confirmed on this cycle.")

                # Wait 15 minutes before next bar cycle
                logging.info("⏳ Sleeping 15 minutes until next 15M bar scan...")
                time.sleep(900)

            else:
                # Market is closed; sleep for 5 minutes and check clock again
                logging.info(f"😴 Market Closed ({status_reason}). Checking again in 5 minutes...")
                time.sleep(300)

        except Exception as e:
            logging.error(f"❌ Unexpected loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
