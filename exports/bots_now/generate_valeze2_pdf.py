#!/usr/bin/env python3
import os

try:
    import reportlab
except ImportError:
    os.system("pip3 install reportlab -q")

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

pdf_filename = "/home/moonrider1968/bots/valeze2_sep2026.pdf"
doc = SimpleDocTemplate(
    pdf_filename,
    pagesize=letter,
    rightMargin=36, leftMargin=36,
    topMargin=36, bottomMargin=36
)

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'DocTitle',
    parent=styles['Heading1'],
    fontSize=18,
    leading=22,
    textColor=colors.HexColor("#0f172a"),
    spaceAfter=3
)
subtitle_style = ParagraphStyle(
    'DocSub',
    parent=styles['Normal'],
    fontSize=9,
    leading=13,
    textColor=colors.HexColor("#475569"),
    spaceAfter=8
)
h2_style = ParagraphStyle(
    'H2',
    parent=styles['Heading2'],
    fontSize=11,
    leading=14,
    textColor=colors.HexColor("#1e293b"),
    spaceBefore=7,
    spaceAfter=3
)
body_style = ParagraphStyle(
    'Body',
    parent=styles['Normal'],
    fontSize=7.5,
    leading=10.5,
    textColor=colors.HexColor("#334155")
)
table_cell = ParagraphStyle(
    'Cell',
    parent=styles['Normal'],
    fontSize=6.8,
    leading=8.5,
    textColor=colors.HexColor("#1e293b")
)
table_header = ParagraphStyle(
    'HeaderCell',
    parent=styles['Normal'],
    fontSize=7.2,
    leading=9,
    fontName="Helvetica-Bold",
    textColor=colors.white
)

elements = []

elements.append(Paragraph("VALEZE2 BOT: PRODUCTION ARCHITECTURE & BENCHMARK AUDIT", title_style))
elements.append(Paragraph("Multi-Strategy Algorithmic Engine | Oliver Velez, Stan Weinstein & Larry Connors Frameworks | September 2026", subtitle_style))
elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#0284c7"), spaceAfter=6))

elements.append(Paragraph("1. System Architecture, Capital & Safety Controls", h2_style))
exec_text = """
<b>System Topology:</b> Deployed on <b>instance-stockbot</b> across two execution environments:
(1) <code>alpaca-velez-scalper.service</code> for morning momentum scalping on NVDA and TSLA (09:45-11:30 AM EDT);
(2) <code>alpaca-swing-daily.timer</code> executing <code>trade_velez_weinstein_swing.py</code> daily at 4:05 PM EDT across 67 symbols.<br/>
<b>Risk Rules:</b> Max 10 concurrent slots, 0.50% account equity risk per trade, independent native Alpaca bracket orders (SL/TP),
and a strict 1.0% intraday portfolio circuit breaker with automated liquidation.
"""
elements.append(Paragraph(exec_text, body_style))
elements.append(Spacer(1, 4))

elements.append(Paragraph("2. Empirical Backtest Scorecard ($100k Capital Base, Daily Bars)", h2_style))

data = [
    [Paragraph("Strategy Setup", table_header),
     Paragraph("Universe", table_header),
     Paragraph("Trades", table_header),
     Paragraph("Win Rate", table_header),
     Paragraph("PF", table_header),
     Paragraph("Avg R", table_header),
     Paragraph("Net PnL", table_header),
     Paragraph("Max DD", table_header),
     Paragraph("Hold", table_header),
     Paragraph("Production Status", table_header)],

    [Paragraph("Velez 200-SMA Snapback", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("32", table_cell), Paragraph("59.4%", table_cell), Paragraph("1.78", table_cell), Paragraph("+0.327R", table_cell), Paragraph("+$5,264", table_cell), Paragraph("1.78%", table_cell), Paragraph("2.0d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("Velez Bull 180 Reversal", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("70", table_cell), Paragraph("64.3%", table_cell), Paragraph("1.68", table_cell), Paragraph("+0.210R", table_cell), Paragraph("+$4,947", table_cell), Paragraph("3.89%", table_cell), Paragraph("2.6d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("Stage 2 Base Breakout", table_cell), Paragraph("17 Growth", table_cell), Paragraph("24", table_cell), Paragraph("60.4%", table_cell), Paragraph("1.62", table_cell), Paragraph("+0.220R", table_cell), Paragraph("+$10,864", table_cell), Paragraph("6.81%", table_cell), Paragraph("18.2d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("Weinstein Dry Retest", table_cell), Paragraph("17 Growth", table_cell), Paragraph("38", table_cell), Paragraph("52.6%", table_cell), Paragraph("1.51", table_cell), Paragraph("+0.250R", table_cell), Paragraph("+$4,644", table_cell), Paragraph("2.99%", table_cell), Paragraph("9.4d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("Connors RSI(2) Strict (&lt;5)", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("249", table_cell), Paragraph("64.3%", table_cell), Paragraph("1.45", table_cell), Paragraph("+0.072R", table_cell), Paragraph("+$9,035", table_cell), Paragraph("3.45%", table_cell), Paragraph("3.4d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("20-SMA Dynamic Bounce", table_cell), Paragraph("17 Growth", table_cell), Paragraph("20", table_cell), Paragraph("45.0%", table_cell), Paragraph("1.50", table_cell), Paragraph("+0.180R", table_cell), Paragraph("+$2,356", table_cell), Paragraph("4.12%", table_cell), Paragraph("12.1d", table_cell), Paragraph("<b>Active Production</b>", table_cell)],
    [Paragraph("Connors RSI(2) Moderate (&lt;10)", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("463", table_cell), Paragraph("66.1%", table_cell), Paragraph("1.38", table_cell), Paragraph("+0.060R", table_cell), Paragraph("+$14,297", table_cell), Paragraph("5.31%", table_cell), Paragraph("3.4d", table_cell), Paragraph("Calibrated", table_cell)],
    [Paragraph("15-Min ORB + VWAP (NVDA/TSLA)", table_cell), Paragraph("Intraday 5M", table_cell), Paragraph("49", table_cell), Paragraph("53.1%", table_cell), Paragraph("1.65", table_cell), Paragraph("+0.140R", table_cell), Paragraph("+$2,765", table_cell), Paragraph("2.10%", table_cell), Paragraph("50m", table_cell), Paragraph("<b>Active Scalper</b>", table_cell)],
    [Paragraph("Connors Double 7s", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("373", table_cell), Paragraph("63.0%", table_cell), Paragraph("1.28", table_cell), Paragraph("+0.083R", table_cell), Paragraph("+$15,544", table_cell), Paragraph("7.06%", table_cell), Paragraph("7.3d", table_cell), Paragraph("Evaluated", table_cell)],
    [Paragraph("Unfiltered 20-SMA Bounce", table_cell), Paragraph("55 Sectors", table_cell), Paragraph("160", table_cell), Paragraph("48.8%", table_cell), Paragraph("1.12", table_cell), Paragraph("+0.041R", table_cell), Paragraph("+$2,741", table_cell), Paragraph("9.42%", table_cell), Paragraph("14.5d", table_cell), Paragraph("Rejected (Chop)", table_cell)],
    [Paragraph("Velez Momentum (1-Bar Trail)", table_cell), Paragraph("17 Growth", table_cell), Paragraph("244", table_cell), Paragraph("34.0%", table_cell), Paragraph("1.11", table_cell), Paragraph("+0.036R", table_cell), Paragraph("+$3,951", table_cell), Paragraph("8.10%", table_cell), Paragraph("4.2d", table_cell), Paragraph("Daily Rejected", table_cell)],
    [Paragraph("Velez Elephant Bar Ignition", table_cell), Paragraph("17 Growth", table_cell), Paragraph("9", table_cell), Paragraph("33.3%", table_cell), Paragraph("0.94", table_cell), Paragraph("-0.031R", table_cell), Paragraph("-$113", table_cell), Paragraph("1.03%", table_cell), Paragraph("3.4d", table_cell), Paragraph("Daily Rejected", table_cell)],
]

t = Table(data, colWidths=[98, 48, 25, 34, 22, 34, 38, 32, 25, 84])
t.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ('TOPPADDING', (0, 0), (-1, -1), 2),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
]))
elements.append(t)
elements.append(Spacer(1, 5))

elements.append(Paragraph("3. Production Setup Profiles in trade_velez_weinstein_swing.py", h2_style))
setups_text = """
<b>1. Weinstein Stage 2 Base Breakout (Growth):</b> Price &gt; 40-day high, Volume &ge; 1.4x 20-SMA vol, Mansfield RS &gt; 0, rising 150-SMA. Stop: 0.4x ATR below base low. Target: +2.0R / 2.5x ATR extension.<br/>
<b>2. Weinstein Stage 2 Dry-Volume Retest (Growth):</b> Pullback tests within 0.5x ATR of 20-SMA with volume &le; 0.75x 20-SMA vol. Reversal bar triggers entry. Stop: 0.4x ATR below dry low. Target: +2.0R.<br/>
<b>3. Velez 20-SMA Dynamic Bounce (Growth):</b> Tested within 0.4x ATR of rising 20-SMA, bar closes green above prior high and 20-SMA. Target: +2.0R.<br/>
<b>4. Velez 200-SMA Snapback (55 Sectors):</b> Flat/rising 200-SMA, price tests within 0.35x ATR or undercuts with RSI2 &le; 25, Bottoming Tail or top 30% close back above 200-SMA. Target: 20-SMA / +1.5R. Stop: 0.15 below low.<br/>
<b>5. Velez Bull 180 Reversal (55 Sectors):</b> 3+ down bars, red Elephant erased &ge;90% by green Elephant. Stop: 0.10 below 2-bar low. Target: +1.5R / 20-SMA.<br/>
<b>6. Connors RSI(2) Strict Panic (55 Sectors):</b> Close &gt; 200-SMA with RSI2 &lt; 5.0. Stop: 2.5x ATR below entry. Target: RSI2 &ge; 70 or Close &ge; 5-SMA.
"""
elements.append(Paragraph(setups_text, body_style))
elements.append(Spacer(1, 4))

elements.append(Paragraph("4. Position Sizing & Safety Execution Logic", h2_style))
sizing_text = """
• <b>Dynamic Share Sizing:</b> <code>Shares = int((Portfolio_Equity * 0.005) / (Entry - Stop_Loss))</code>.<br/>
• <b>Broker Compliance:</b> Executed via native Alpaca <code>OrderClass.BRACKET</code> with <code>TimeInForce.DAY</code>.<br/>
• <b>Intraday Circuit Breaker:</b> Evaluates <code>(equity - last_equity) / last_equity &le; -0.010</code>. Trips hard liquidation if triggered.
"""
elements.append(Paragraph(sizing_text, body_style))

doc.build(elements)
print(f"[+] PDF generated successfully: {pdf_filename}")
