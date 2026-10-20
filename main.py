
import os
import math
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timezone, timedelta

BASE_URL = "https://biquote.io/api"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TIMEOUT = 20

session = requests.Session()
session.headers.update({"User-Agent": "GoldSignalBot/1.0"})


def get_json(url, params=None):
    r = session.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_candles(interval):
    data = get_json(
        f"{BASE_URL}/XAUUSD/ohlc",
        {"interval": interval, "limit": 180}
    )

    bars = data.get("bars", []) if isinstance(data, dict) else data
    if not isinstance(bars, list) or not bars:
        raise ValueError(f"کندل‌های {interval} دریافت نشد")

    bars = [b for b in bars if not b.get("isOpen", False)]
    bars.sort(key=lambda b: str(b.get("openTime", "")))

    df = pd.DataFrame(bars)
    required = ["open", "high", "low", "close"]

    for col in required:
        if col not in df:
            raise ValueError(f"ستون {col} در داده طلا نیست")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required).reset_index(drop=True)

    if len(df) < 60:
        raise ValueError(f"کندل کافی برای {interval} نداریم")

    return df


def analyze(interval):
    df = get_candles(interval)
    c, h, l = df["close"], df["high"], df["low"]

    # 1. میانگین‌های متحرک 20 و 50
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    # دامنه واقعی قیمت برای ATR و ADX
    tr = pd.concat([
        h - l,
        (h - c.shift()).abs(),
        (l - c.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/14, adjust=False).mean()

    # 2. RSI
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1/14, min_periods=14, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1/14, min_periods=14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.fillna(100).where(avg_gain > 0, 0)

    # 3. MACD
    macd = (
        c.ewm(span=12, adjust=False).mean()
        - c.ewm(span=26, adjust=False).mean()
    )
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    # 4. ADX و جهت حرکت مثبت/منفی
    up_move = h.diff()
    down_move = -l.diff()

    plus_dm = up_move.where(
        (up_move > down_move) & (up_move > 0), 0.0
    )
    minus_dm = down_move.where(
        (down_move > up_move) & (down_move > 0), 0.0
    )

    smoothed_tr = tr.ewm(alpha=1/14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1/14, adjust=False
    ).mean() / smoothed_tr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(
        alpha=1/14, adjust=False
    ).mean() / smoothed_tr.replace(0, float("nan"))

    dx = 100 * (plus_di - minus_di).abs() / (
        plus_di + minus_di
    ).replace(0, float("nan"))
    adx = dx.ewm(alpha=1/14, adjust=False).mean()

    # 5. باندهای بولینگر
    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std(ddof=0)
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    i = len(df) - 1

    result = {
        "price": float(c.iloc[i]),
        "ema20": float(ema20.iloc[i]),
        "ema50": float(ema50.iloc[i]),
        "rsi": float(rsi.iloc[i]),
        "atr": float(atr.iloc[i]),
        "macd": float(macd.iloc[i]),
        "macd_signal": float(macd_signal.iloc[i]),
        "adx": float(adx.iloc[i]),
        "plus_di": float(plus_di.iloc[i]),
        "minus_di": float(minus_di.iloc[i]),
        "bb_mid": float(bb_mid.iloc[i]),
        "bb_upper": float(bb_upper.iloc[i]),
        "bb_lower": float(bb_lower.iloc[i]),
        "support": float(l.tail(20).min()),
        "resistance": float(h.tail(20).max()),
    }

    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError(f"اندیکاتور نامعتبر در تایم‌فریم {interval}")

    result["trend"] = (
        "صعودی" if result["ema20"] > result["ema50"]
        else "نزولی" if result["ema20"] < result["ema50"]
        else "خنثی"
    )

    return result


def find_number(obj, keys):
    """یافتن عدد در پاسخ‌های متفاوت API."""
    if isinstance(obj, dict):
        for key in keys:
            for actual_key, value in obj.items():
                if actual_key.lower() == key.lower():
                    try:
                        if value is not None:
                            number = float(value)
                            if math.isfinite(number):
                                return number
                    except (TypeError, ValueError):
                        pass

        for value in obj.values():
            result = find_number(value, keys)
            if result is not None:
                return result

    elif isinstance(obj, list):
        for value in obj:
            result = find_number(value, keys)
            if result is not None:
                return result

    return None


def get_dxy():
    try:
        data = get_json(f"{BASE_URL}/DXY")
        price = find_number(
            data, data, ["mid", "bid", "ask", "price", "close", "value"]
        )
        change = find_number(
            data, ["changePercent", "change_pct",
                   "dailyChange", "percentChange"]
        )

        if price is None:
            return {"status": "قیمت DXY در پاسخ پیدا نشد"}

        return {
            "status": "دریافت شد",
            "price": price,
            "change": change
        }
    except Exception as e:
        return {"status": f"ناموجود ({type(e).__name__})"}


def get_fred_yield(series_id):
    """دریافت بازده اوراق از داده روزانه FRED."""
    try:
        url = (
            "https://fred.stlouisfed.org/graph/fredgraph.csv"
            f"?id={series_id}"
        )
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()

        df = pd.read_csv(StringIO(r.text))
        if len(df.columns) < 2:
            raise ValueError("قالب CSV نامعتبر")

        values = pd.to_numeric(
            df.iloc[:, 1].replace(".", pd.NA), errors="coerce"
        ).dropna()

        if len(values) < 2:
            raise ValueError("داده کافی نیست")

        latest = float(values.iloc[-1])
        previous = float(values.iloc[-2])

        return {
            "status": "دریافت شد",
            "value": latest,
            "change": latest - previous
        }
    except Exception as e:
        return {"status": f"ناموجود ({type(e).__name__})"}


def parse_event_time(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
        return dt
    except (ValueError, TypeError):
        return None


def get_news():
    """ابتدا تقویم عمومی Forex Factory؛ سپس منبع جایگزین."""
    sources = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        f"{BASE_URL}/calendar"
    ]

    for url in sources:
        try:
            data = get_json(url)

            if isinstance(data, dict):
                events = data.get(
                    "events", data.get("calendar", data.get("data", []))
                )
            else:
                events = data

            if not isinstance(events, list):
                continue

            relevant = []
            now = datetime.now(timezone.utc)

            for event in events:
                if not isinstance(event, dict):
                    continue

                currency = str(
                    event.get("country", event.get(
                        "currency", event.get("Currency", "")
                    ))
                ).upper()

                if currency not in ("USD", "US", "UNITED STATES"):
                    continue

                title = event.get(
                    "title", event.get("event", event.get("name", "خبر USD"))
                )
                impact = str(event.get("impact", "نامشخص"))
                date_text = event.get(
                    "date", event.get("datetime", event.get("time", ""))
                )

                dt = parse_event_time(date_text)
                minutes = None

                # فقط ساعت دارای منطقه زمانی برای محاسبه فاصله معتبر است.
                if dt is not None and dt.tzinfo is not None:
                    minutes = (
                        dt.astimezone(timezone.utc) - now
                    ).total_seconds() / 60

                relevant.append({
                    "title": str(title),
                    "impact": impact,
                    "date": str(date_text),
                    "minutes": minutes
                })

            if relevant:
                return {
                    "status": "دریافت شد",
                    "events": relevant[:10],
                    "source": url
                }

        except Exception:
            continue

    return {"status": "تقویم خبری در دسترس نیست", "events": []}


def fmt_yield(label, data):
    if "value" not in data:
        return f"{label}: {data['status']}"

    change = data["change"]
    direction = (
        "افزایش" if change > 0
        else "کاهش" if change < 0
        else "بدون تغییر"
    )
    return (
        f"{label}: {data['value']:.3f}% | "
        f"تغییر نسبت به مشاهده قبلی: {change:+.3f} واحد درصد ({direction})"
    )


def make_signal(m15, h1, h4, dxy, y2, y10, news):
    buy_votes = sum([
        m15["ema20"] > m15["ema50"],
        m15["macd"] > m15["macd_signal"],
        50 < m15["rsi"] < 70,
        m15["adx"] >= 18 and m15["plus_di"] > m15["minus_di"],
        m15["price"] > m15["bb_mid"]
    ])

    sell_votes = sum([
        m15["ema20"] < m15["ema50"],
        m15["macd"] < m15["macd_signal"],
        30 < m15["rsi"] < 50,
        m15["adx"] >= 18 and m15["minus_di"] > m15["plus_di"],
        m15["price"] < m15["bb_mid"]
    ])

    bullish_tf = sum(
        x["ema20"] > x["ema50"] for x in [m15, h1, h4]
    )
    bearish_tf = sum(
        x["ema20"] < x["ema50"] for x in [m15, h1, h4]
    )

    # خبر پراثر USD در 45 دقیقه آینده: از ورود جدید خودداری شود.
    blocked_news = [
        e for e in news.get("events", [])
        if "high" in e["impact"].lower()
        and e["minutes"] is not None
        and 0 <= e["minutes"] <= 45
    ]

    entry = m15["price"]
    atr = m15["atr"]

    if blocked_news:
        return "عدم ورود؛ خبر مهم USD در 45 دقیقه آینده", None

    # در صورت تعارض روند تایم‌فریم‌ها، سیگنال صادر نشود.
    if buy_votes >= 4 and bullish_tf >= 2:
        direction = "خرید"
        sl = entry - 1.5 * atr
        tp1 = entry + 2 * atr
        tp2 = entry + 3 * atr
    elif sell_votes >= 4 and bearish_tf >= 2:
        direction = "فروش"
        sl = entry + 1.5 * atr
        tp1 = entry - 2 * atr
        tp2 = entry - 3 * atr
    else:
        return "عدم ورود؛ شروط تکنیکال هم‌جهت نیستند", None

    # تضاد آشکار دو منبع کلان، سیگنال را به حالت انتظار می‌برد.
    macro = []

    if dxy.get("change") is not None:
        macro.append("buy" if dxy["change"] < 0 else
                     "sell" if dxy["change"] > 0 else "neutral")

    for y in (y2, y10):
        if "change" in y:
            macro.append("buy" if y["change"] < 0 else
                         "sell" if y["change"] > 0 else "neutral")

    if "buy" in macro and "sell" in macro:
        return "عدم ورود؛ داده‌های کلان متناقض‌اند", None

    return direction, {
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
        "buy_votes": buy_votes, "sell_votes": sell_votes
    }


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    r = session.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": True
        },
        timeout=TIMEOUT
    )

    if not r.ok:
        raise RuntimeError(
            f"خطای تلگرام HTTP {r.status_code}: {r.text[:500]}"
        )


def main():
    m15 = analyze("15m")
    h1 = analyze("1h")
    h4 = analyze("4h")

    dxy = get_dxy()
    y2 = get_fred_yield("DGS2")
    y10 = get_fred_yield("DGS10")
    news = get_news()

    direction, levels = make_signal(
        m15, h1, h4, dxy, y2, y10, news
    )

    lines = [
        "تحلیل ربات طلای XAUUSD",
        f"زمان گزارش UTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        f"قیمت آخرین کندل بسته‌شده: {m15['price']:.2f}",
        "",
        "روند تایم‌فریم‌ها:",
        f"15m: {m15['trend']} | 1h: {h1['trend']} | 4h: {h4['trend']}",
        "",
        "اندیکاتورهای 15m:",
        f"MA20: {m15['ema20']:.2f} | MA50: {m15['ema50']:.2f}",
        f"RSI: {m15['rsi']:.1f} | ADX: {m15['adx']:.1f}",
        f"+DI: {m15['plus_di']:.1f} | -DI: {m15['minus_di']:.1f}",
        f"ATR: {m15['atr']:.2f}",
        f"MACD: {m15['macd']:.3f} | سیگنال MACD: {m15['macd_signal']:.3f}",
        f"Bollinger بالا: {m15['bb_upper']:.2f}",
        f"Bollinger میانی: {m15['bb_mid']:.2f}",
        f"Bollinger پایین: {m15['bb_lower']:.2f}",
        "",
        f"حمایت تقریبی: {m15['support']:.2f}",
        f"مقاومت تقریبی: {m15['resistance']:.2f}",
        "",
        "داده‌های کلان:",
        (
            f"DXY: {dxy['price']:.3f}"
            + (
                f" | تغییر گزارش‌شده: {dxy['change']:+.3f}"
                if dxy.get("change") is not None else ""
            )
            if "price" in dxy else f"DXY: {dxy['status']}"
        ),
        fmt_yield("اوراق 2 ساله آمریکا", y2),
        fmt_yield("اوراق 10 ساله آمریکا", y10),
        "",
        f"وضعیت تقویم خبری: {news['status']}",
    ]

    if news.get("events"):
        lines.append("اخبار USD:")
        for event in news["events"][:5]:
            lines.append(
                f"- {event['title']} | اثر: {event['impact']} | "
                f"زمان منبع: {event['date'] or 'نامشخص'}"
            )
    else:
        lines.append("اخبار: هیچ رویداد قابل‌تأییدی دریافت نشد.")

    lines += ["", f"نتیجه: {direction}"]

    if levels:
        lines += [
            f"ورود مرجع: {levels['entry']:.2f}",
            f"حد ضرر (SL): {levels['sl']:.2f}",
            f"هدف اول (TP1): {levels['tp1']:.2f}",
            f"هدف دوم (TP2): {levels['tp2']:.2f}",
            f"رأی تکنیکال خرید/فروش: {levels['buy_votes']}/5 و {levels['sell_votes']}/5",
        ]

    lines += [
        "",
        "هشدار: این گزارش سیگنال آزمایشی است، نه تضمین سود.",
        "اوراق FRED روزانه‌اند؛ زمان آخرین مشاهده با زمان گزارش یکسان نیست."
    ]

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # خطای واقعی در GitHub Actions ثبت می‌شود.
        print(f"BOT ERROR: {type(e).__name__}: {e}")
        raise
