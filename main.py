
import os
import requests
import pandas as pd
from datetime import datetime, timezone

BASE_URL = "https://biquote.io/api"
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TIMEOUT = 20


def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_candles(interval):
    data = get_json(
        f"{BASE_URL}/XAUUSD/ohlc",
        {"interval": interval, "limit": 180}
    )

    bars = data.get("bars", []) if isinstance(data, dict) else data
    bars = [b for b in bars if not b.get("isOpen", False)]
    bars.sort(key=lambda b: b.get("openTime", ""))

    df = pd.DataFrame(bars)
    if df.empty:
        raise ValueError(f"داده کندل {interval} دریافت نشد")

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) < 60:
        raise ValueError(f"داده کافی برای {interval} وجود ندارد")

    return df.reset_index(drop=True)


def analyze(interval):
    df = get_candles(interval)
    c, h, l = df["close"], df["high"], df["low"]

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    prev_close = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_close).abs(),
        (l - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    plus_dm = h.diff()
    minus_dm = -l.diff()
    plus_dm = plus_dm.where(
        (plus_dm > minus_dm) & (plus_dm > 0), 0.0
    )
    minus_dm = minus_dm.where(
        (minus_dm > plus_dm) & (minus_dm > 0), 0.0
    )

    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(
        alpha=1 / 14, adjust=False
    ).mean() / atr14.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(
        alpha=1 / 14, adjust=False
    ).mean() / atr14.replace(0, float("nan"))

    dx = 100 * (plus_di - minus_di).abs() / (
        plus_di + minus_di
    ).replace(0, float("nan"))
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    macd = (
        c.ewm(span=12, adjust=False).mean()
        - c.ewm(span=26, adjust=False).mean()
    )
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std

    i = len(df) - 1

    def val(series):
        x = series.iloc[i]
        return float(x) if pd.notna(x) else 0.0

    return {
        "price": val(c),
        "ema20": val(ema20),
        "ema50": val(ema50),
        "rsi": val(rsi),
        "atr": val(atr),
        "macd": val(macd),
        "macd_signal": val(macd_signal),
        "adx": val(adx),
        "plus_di": val(plus_di),
        "minus_di": val(minus_di),
        "bb_upper": val(bb_upper),
        "bb_mid": val(bb_mid),
        "bb_lower": val(bb_lower),
        "support": float(l.tail(20).min()),
        "resistance": float(h.tail(20).max()),
    }


def get_dxy():
    try:
        data = get_json(f"{BASE_URL}/DXY")

        if not isinstance(data, dict):
            return None, None, "پاسخ نامعتبر"

        # mid اولویت دارد؛ last در این API ممکن است صفر باشد.
        price = None
        for key in ["mid", "bid", "ask", "price", "close", "value"]:
            try:
                v = float(data.get(key))
                if v > 0:
                    price = v
                    break
            except (TypeError, ValueError):
                continue

        change = None
        for key in [
            "dayDiffPercent", "changePercent",
            "change_pct", "dailyChange", "percentChange"
        ]:
            try:
                if data.get(key) is not None:
                    change = float(data[key])
                    break
            except (TypeError, ValueError):
                continue

        stale = data.get("stale", False)
        status = "داده قدیمی است" if stale else "دریافت شد"
        return price, change, status

    except Exception as e:
        return None, None, f"خطا: {str(e)[:100]}"


def get_yield(series_id):
    url = (
        "https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={series_id}"
    )
    try:
        df = pd.read_csv(url)
        if len(df.columns) < 2:
            return None, None, None

        date_col, value_col = df.columns[0], df.columns[1]
        df[value_col] = pd.to_numeric(
            df[value_col], errors="coerce"
        )
        df = df.dropna(subset=[value_col])

        if len(df) < 1:
            return None, None, None

        latest = float(df[value_col].iloc[-1])
        date = str(df[date_col].iloc[-1])
        change = None

        if len(df) >= 2:
            change = latest - float(df[value_col].iloc[-2])

        return latest, change, date

    except Exception:
        return None, None, None


def get_calendar():
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        f"{BASE_URL}/calendar"
    ]

    for url in urls:
        try:
            data = get_json(url)
            if isinstance(data, dict):
                data = data.get("events", data.get("data", []))
            if isinstance(data, list):
                events = []
                for e in data:
                    if not isinstance(e, dict):
                        continue
                    currency = str(
                        e.get("country", e.get("currency", ""))
                    ).upper()
                    if currency not in ["USD", "US"]:
                        continue

                    impact = str(e.get("impact", "")).lower()
                    title = (
                        e.get("title")
                        or e.get("event")
                        or e.get("name")
                        or "خبر USD"
                    )
                    when = (
                        e.get("date")
                        or e.get("datetime")
                        or e.get("time")
                        or "زمان نامشخص"
                    )
                    events.append((str(impact), str(title), str(when)))

                return events[:8], "دریافت شد"
        except Exception:
            continue

    return [], "در دسترس نیست"


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message[:4000],
            "disable_web_page_preview": True
        },
        timeout=TIMEOUT
    )
    if not r.ok:
        raise RuntimeError(
            f"Telegram error {r.status_code}: {r.text[:300]}"
        )


def trend(x):
    if x["ema20"] > x["ema50"]:
        return "صعودی"
    if x["ema20"] < x["ema50"]:
        return "نزولی"
    return "خنثی"


def main():
    m15 = analyze("15m")
    h1 = analyze("1h")
    h4 = analyze("4h")

    # رأی تکنیکال در تایم‌فریم 15 دقیقه
    buy_score = 0
    sell_score = 0

    for x in [m15, h1, h4]:
        if x["ema20"] > x["ema50"]:
            buy_score += 1
        elif x["ema20"] < x["ema50"]:
            sell_score += 1

    if m15["macd"] > m15["macd_signal"]:
        buy_score += 1
    elif m15["macd"] < m15["macd_signal"]:
        sell_score += 1

    if m15["plus_di"] > m15["minus_di"]:
        buy_score += 1
    elif m15["plus_di"] < m15["minus_di"]:
        sell_score += 1

    rsi = m15["rsi"]
    if 50 <= rsi < 70:
        buy_score += 1
    elif 30 < rsi < 50:
        sell_score += 1

    entry = m15["price"]
    atr = m15["atr"]

    if buy_score >= 4 and buy_score > sell_score and rsi < 70:
        direction = "خرید"
        sl = entry - 1.5 * atr
        tp1 = entry + 2 * atr
        tp2 = entry + 3 * atr
    elif sell_score >= 4 and sell_score > buy_score and rsi > 30:
        direction = "فروش"
        sl = entry + 1.5 * atr
        tp1 = entry - 2 * atr
        tp2 = entry - 3 * atr
    else:
        direction = "بدون سیگنال قطعی"
        sl = tp1 = tp2 = None

    dxy, dxy_change, dxy_status = get_dxy()
    y2, y2_change, y2_date = get_yield("DGS2")
    y10, y10_change, y10_date = get_yield("DGS10")
    events, calendar_status = get_calendar()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "تحلیل ربات طلای XAUUSD",
        f"زمان گزارش: {now}",
        f"قیمت آخرین کندل بسته‌شده: {entry:.2f}",
        "",
        "روند تایم‌فریم‌ها:",
        f"15m: {trend(m15)} | 1h: {trend(h1)} | 4h: {trend(h4)}",
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
        "داده‌های کلان:"
    ]

    lines.append(
        f"DXY: {dxy:.3f}" if dxy is not None
        else "DXY: دریافت نشد"
    )
    if dxy_change is not None:
        lines.append(f"تغییر روزانه DXY: {dxy_change:+.4f}%")
    lines.append(f"وضعیت DXY: {dxy_status}")

    lines.append(
        f"اوراق 2 ساله آمریکا: {y2:.3f}%"
        + (f" | تغییر: {y2_change:+.3f} واحد درصد" if y2_change is not None else "")
        + (f" | تاریخ داده: {y2_date}" if y2_date else "")
        if y2 is not None else "اوراق 2 ساله آمریکا: دریافت نشد"
    )
    lines.append(
        f"اوراق 10 ساله آمریکا: {y10:.3f}%"
        + (f" | تغییر: {y10_change:+.3f} واحد درصد" if y10_change is not None else "")
        + (f" | تاریخ داده: {y10_date}" if y10_date else "")
        if y10 is not None else "اوراق 10 ساله آمریکا: دریافت نشد"
    )

    lines.extend(["", f"وضعیت تقویم خبری: {calendar_status}"])
    if events:
        lines.append("اخبار USD:")
        for impact, title, when in events[:5]:
            lines.append(f"- {title} | اثر: {impact} | زمان: {when}")
    else:
        lines.append("اخبار USD: در این اجرا دریافت نشد")

    lines.extend([
        "",
        f"نتیجه: {direction}",
        f"رأی تکنیکال خرید/فروش: {buy_score}/{sell_score}"
    ])

    if sl is not None:
        lines.extend([
            f"ورود مرجع: {entry:.2f}",
            f"حد ضرر (SL): {sl:.2f}",
            f"هدف اول (TP1): {tp1:.2f}",
            f"هدف دوم (TP2): {tp2:.2f}"
        ])

    lines.extend([
        "",
        "هشدار: این گزارش آزمایشی است و سود را تضمین نمی‌کند.",
        "اوراق FRED روزانه‌اند و ممکن است با زمان گزارش هم‌زمان نباشند."
    ])

    send_telegram("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # خطای اجرا را هم در Actions ثبت می‌کند.
        print(f"ERROR: {type(e).__name__}: {e}")
        raise
