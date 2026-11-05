"""
Trend Confidence
"""

class TrendConfidence:

    def score(self, stage, mansfield, ema20, sma150):

        score = 0

        if stage == 2:
            score += 30

        score += mansfield
        score += ema20
        score += sma150

        return min(score,100)
