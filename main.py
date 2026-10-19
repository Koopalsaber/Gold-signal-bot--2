import os
import requests
import pandas as pd

BASE_URL = "https://biquote.io/api"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def get_candles(interval):
    url = f"{BASE_URL}/XAUUSD/ohlc"
    r = requests.get(
        url,
        params={"interval": interval, "limit": 120},
        timeout=20
    )
    r.raise_for_status()
    bars = r.json()["bars"]

    # فقط کندل‌های بسته‌شده
    bars = [b for b in bars if not b.get("isOpen", False)]
    bars.sort(key=lambda b: b["openTime"])

    df = pd.DataFrame(bars)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col])

    return df.reset_index(drop=True)

def analyze(interval):
    df = get_candles(interval)

    if len(df) < 60:
        raise ValueError(f"داده کافی نیست: {interval}")

    c = df["close"]
    h = df["high"]
    l = df["low"]

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, min_periods=14,
                        adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, min_periods=14,
                        adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    macd = (
        c.ewm(span=12, adjust=False).mean()
        - c.ewm(span=26, adjust=False).mean()
    )
    signal_line = macd.ewm(span=9, adjust=False).mean()

    i = len(df) - 1
    return {
        "price": float(c.iloc[i]),
        "ema20": float(ema20.iloc[i]),
        "ema50": float(ema50.iloc[i]),
        "rsi": float(rsi.iloc[i]),
        "atr": float(atr.iloc[i]),
        "macd": float(macd.iloc[i]),
        "macd_signal": float(signal_line.iloc[i]),
        "support": float(l.tail(20).min()),
        "resistance": float(h.tail(20).max())
    }

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": message},
        timeout=20
    )
    r.raise_for_status()

def main():
    m15 = analyze("15m")
    h1 = analyze("1h")
    h4 = analyze("4h")

    bullish = sum(
        x["ema20"] > x["ema50"] for x in [m15, h1, h4]
    )
    bearish = sum(
        x["ema20"] < x["ema50"] for x in [m15, h1, h4]
    )

    entry = m15["price"]
    atr = m15["atr"]
    rsi = m15["rsi"]

    if bullish >= 2 and m15["macd"] > m15["macd_signal"] \
            and 50 < rsi < 70:
        direction = "خرید"
        sl = entry - 1.5 * atr
        tp1 = entry + 2 * atr
        tp2 = entry + 3 * atr
    elif bearish >= 2 and m15["macd"] < m15["macd_signal"] \
            and 30 < rsi < 50:
        direction = "فروش"
        sl = entry + 1.5 * atr
        tp1 = entry - 2 * atr
        tp2 = entry - 3 * atr
    else:
        direction = "بدون سیگنال قطعی"
        sl = tp1 = tp2 = None

    msg = (
        "تحلیل ربات طلای XAUUSD\n"
        f"قیمت آخرین کندل بسته‌شده: {entry:.2f}\n"
        f"RSI پانزده‌دقیقه: {rsi:.1f}\n"
        f"حمایت تقریبی: {m15['support']:.2f}\n"
        f"مقاومت تقریبی: {m15['resistance']:.2f}\n"
        f"روند 15m / 1h / 4h: {bullish} صعودی، {bearish} نزولی\n"
        f"نتیجه: {direction}"
    )

    if sl is not None:
        msg += (
            f"\nورود مرجع: {entry:.2f}"
            f"\nحد ضرر: {sl:.2f}"
            f"\nهدف اول: {tp1:.2f}"
            f"\nهدف دوم: {tp2:.2f}"
        )

    send_telegram(msg)

if __name__ == "__main__":
    main()
