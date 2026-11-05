import os, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv('/home/moonrider1968/bots/killer_beast_sniper/.env')
API_KEY = os.getenv('APCA_API_KEY_ID')
SECRET_KEY = os.getenv('APCA_API_SECRET_KEY')
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# Keyword scoring weights
BEARISH_KEYWORDS = [
    'downgrade', 'downgraded', 'miss', 'misses', 'investigation', 'probe',
    'lawsuit', 'subpoena', 'offering', 'dilution', 'fraud', 'halts', 'plunges',
    'cuts guidance', 'lowers guidance', 'warning', 'defaults'
]
BULLISH_KEYWORDS = [
    'upgrade', 'upgraded', 'beats', 'beat', 'raises guidance', 'boosts',
    'fda approval', 'contract win', 'record revenue', 'buyback', 'partnership',
    'outperform', 'overweight', 'price target raised'
]

def analyze_headline_sentiment(headline: str) -> int:
    score = 0
    lower_h = headline.lower()
    for kw in BULLISH_KEYWORDS:
        if kw in lower_h:
            score += 1
    for kw in BEARISH_KEYWORDS:
        if kw in lower_h:
            score -= 2 # Heavily penalize toxic catalysts
    return score

def check_stock_news_catalyst(symbol: str) -> dict:
    """
    Scans the last 24 hours of news headlines for toxicity or bullish catalysts.
    Returns: {'passed': bool, 'sentiment_score': int, 'recent_headlines': list, 'reason': str}
    """
    headers = {'APCA-API-KEY-ID': API_KEY, 'APCA-API-SECRET-KEY': SECRET_KEY}
    start_time = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')
    
    params = {
        'symbols': symbol,
        'start': start_time,
        'limit': 10,
        'include_content': 'false'
    }
    
    try:
        res = requests.get(NEWS_URL, headers=headers, params=params, timeout=5)
        if res.status_code != 200:
            return {'passed': True, 'sentiment_score': 0, 'reason': 'News API skipped'}
            
        news_items = res.json().get('news', [])
        if not news_items:
            return {'passed': True, 'sentiment_score': 0, 'reason': 'No recent breaking news'}

        total_score = 0
        toxic_detected = False
        toxic_headline = ""

        for item in news_items:
            h = item.get('headline', '')
            score = analyze_headline_sentiment(h)
            total_score += score
            if score <= -2:
                toxic_detected = True
                toxic_headline = h
                break

        if toxic_detected:
            return {
                'passed': False,
                'sentiment_score': total_score,
                'reason': f"TOXIC NEWS BLOCKED: {toxic_headline}"
            }

        return {
            'passed': True,
            'sentiment_score': total_score,
            'reason': f"News Clear (Score: {total_score})"
        }

    except Exception as e:
        return {'passed': True, 'sentiment_score': 0, 'reason': f'Error fetching news: {e}'}

if __name__ == '__main__':
    test_tickers = ['NVDA', 'AAPL', 'TSLA', 'LLY']
    print("=" * 60)
    print("📰 REAL-TIME ALPACA / BENZINGA NEWS CATALYST SCAN")
    print("=" * 60)
    for t in test_tickers:
        res = check_stock_news_catalyst(t)
        status = "🟢 PASS" if res['passed'] else "🔴 BLOCKED"
        print(f"{status} | {t:<5} | {res['reason']}")
    print("=" * 60)
