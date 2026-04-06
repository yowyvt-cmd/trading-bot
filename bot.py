import requests
import pandas as pd
import time

BALANCE = 200
RISK_PER_TRADE = 5
MAX_TRADES = 15

MAX_PER_SYMBOL_SIDE = 2
MIN_DISTANCE = 0.003
COOLDOWN = 300

SYMBOLS = [
    "BTC-USDT","ETH-USDT","BNB-USDT",
    "SOL-USDT","XRP-USDT","ADA-USDT",
    "DOGE-USDT","AVAX-USDT","DOT-USDT",
    "LINK-USDT","LTC-USDT","ATOM-USDT",
    "NEAR-USDT","FIL-USDT","APT-USDT"
]

INTERVAL = "5m"

positions = []
last_trade_time = {}

closed_trades = 0
tp1_hits = 0
tp2_hits = 0
tp3_hits = 0
tp1_to_be = 0
tp2_to_tp1 = 0
sl_direct = 0


def get_leverage(atr, price):
    vol = atr / price

    if vol < 0.0015:
        return 50
    elif vol < 0.0025:
        return 35
    elif vol < 0.004:
        return 25
    else:
        return 15


def get_price(symbol):
    try:
        p = requests.get(f"https://open-api.bingx.com/openApi/swap/v2/quote/price?symbol={symbol}").json()
        b = requests.get(f"https://open-api.bingx.com/openApi/swap/v2/quote/bookTicker?symbol={symbol}").json()

        price = float(p["data"]["price"])
        bid = float(b["data"]["book_ticker"]["bid_price"])
        ask = float(b["data"]["book_ticker"]["ask_price"])

        return price, bid, ask
    except:
        return None, None, None


def get_data(symbol):
    try:
        r = requests.get(f"https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol={symbol}&interval={INTERVAL}&limit=100").json()
        df = pd.DataFrame(r["data"])
        df = df.rename(columns={"c":"close","h":"high","l":"low","v":"volume"})

        for col in ["close","high","low","volume"]:
            df[col] = df[col].astype(float)

        return df
    except:
        return None


def indicators(df):
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["vol_mean"] = df["volume"].rolling(20).mean()

    # 🔥 RSI añadido (simple)
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


def get_signal(df):
    last = df.iloc[-1]

    trend_strength = abs(last["ema50"] - last["ema200"]) / last["close"]

    if trend_strength < 0.002:
        return None, False

    if last["volume"] < last["vol_mean"]:
        return None, False

    # 🔥 filtro RSI
    if last["rsi"] > 70 or last["rsi"] < 30:
        return None, False

    strong = trend_strength > 0.005

    if last["ema50"] > last["ema200"]:
        return "LONG", strong

    if last["ema50"] < last["ema200"]:
        return "SHORT", strong

    return None, False


def can_trade(symbol, side, entry):
    now = time.time()

    if symbol in last_trade_time and now - last_trade_time[symbol] < COOLDOWN:
        return False

    count = sum(1 for p in positions if p["symbol"] == symbol and p["side"] == side)
    if count >= MAX_PER_SYMBOL_SIDE:
        return False

    for p in positions:
        if p["symbol"] == symbol and p["side"] == side:
            if abs(p["entry"] - entry)/entry < MIN_DISTANCE:
                return False

    return True


def close_opposite(symbol, side, strong):
    if strong:
        return

    for p in positions[:]:
        if p["symbol"] == symbol and p["side"] != side:
            positions.remove(p)


def open_trade(symbol, side, bid, ask, atr, strong):
    global last_trade_time

    if len(positions) >= MAX_TRADES:
        return

    entry = ask if side=="LONG" else bid

    if atr / entry < 0.002:
        return

    lev = get_leverage(atr, entry)

    sl_pct = max((atr * 2.2) / entry, 0.003)

    tp1 = max(sl_pct * 1.5, 0.003)
    tp2 = max(sl_pct * 3, 0.005)
    tp3 = max(sl_pct * 5, 0.008)

    if not can_trade(symbol, side, entry):
        return

    # 🔥 cerrar opuestas si no hay tendencia fuerte
    close_opposite(symbol, side, strong)

    positions.append({
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "tp_pct": [tp1, tp2, tp3],
        "sl_pct": sl_pct,
        "sl_price": None,
        "tp_hits": [False, False, False],
        "lev": lev
    })

    last_trade_time[symbol] = time.time()

    print(f"""
🚀 NUEVA OPERACIÓN
{side} {symbol}

Entry: {entry:.5f}
Leverage: x{lev}

SL: {(entry*(1-sl_pct if side=='LONG' else 1+sl_pct)):.5f}
TP1: {(entry*(1+tp1 if side=='LONG' else 1-tp1)):.5f}
TP2: {(entry*(1+tp2 if side=='LONG' else 1-tp2)):.5f}
TP3: {(entry*(1+tp3 if side=='LONG' else 1-tp3)):.5f}

----------------------
""")


def manage(prices):
    global BALANCE, closed_trades, tp1_hits, tp2_hits, tp3_hits, tp1_to_be, tp2_to_tp1, sl_direct

    for p in positions[:]:
        price, bid, ask = prices[p["symbol"]]

        entry = p["entry"]
        lev = p["lev"]

        tp1 = entry*(1+p["tp_pct"][0]) if p["side"]=="LONG" else entry*(1-p["tp_pct"][0])
        tp2 = entry*(1+p["tp_pct"][1]) if p["side"]=="LONG" else entry*(1-p["tp_pct"][1])
        tp3 = entry*(1+p["tp_pct"][2]) if p["side"]=="LONG" else entry*(1-p["tp_pct"][2])

        sl = p["sl_price"] if p["sl_price"] else entry*(1-p["sl_pct"] if p["side"]=="LONG" else 1+p["sl_pct"])

        if not p["tp_hits"][0]:
            if (p["side"]=="LONG" and bid>=tp1) or (p["side"]=="SHORT" and ask<=tp1):
                BALANCE += RISK_PER_TRADE * lev * p["tp_pct"][0]
                p["tp_hits"][0]=True
                p["sl_price"]=entry
                tp1_hits+=1

        if not p["tp_hits"][1]:
            if (p["side"]=="LONG" and bid>=tp2) or (p["side"]=="SHORT" and ask<=tp2):
                BALANCE += RISK_PER_TRADE * lev * p["tp_pct"][1]
                p["tp_hits"][1]=True
                p["sl_price"]=tp1
                tp2_hits+=1

        if (p["side"]=="LONG" and bid>=tp3) or (p["side"]=="SHORT" and ask<=tp3):
            BALANCE += RISK_PER_TRADE * lev * p["tp_pct"][2]
            tp3_hits+=1
            closed_trades+=1
            positions.remove(p)
            continue

        if (p["side"]=="LONG" and bid<=sl) or (p["side"]=="SHORT" and ask>=sl):

            loss = RISK_PER_TRADE * lev * p["sl_pct"]
            BALANCE -= loss

            closed_trades+=1

            if p["tp_hits"][1]:
                tp2_to_tp1+=1
            elif p["tp_hits"][0]:
                tp1_to_be+=1
            else:
                sl_direct+=1

            positions.remove(p)


while True:

    prices={}

    for s in SYMBOLS:
        df = get_data(s)
        if df is None:
            continue

        df = indicators(df)
        signal, strong = get_signal(df)

        price, bid, ask = get_price(s)
        if not price:
            continue

        atr = df.iloc[-1]["atr"]
        prices[s]=(price,bid,ask)

        if signal:
            open_trade(s, signal, bid, ask, atr, strong)

    manage(prices)

    print(f"""
==============================
💼 BALANCE: {BALANCE:.2f}
📊 OPEN: {len(positions)}
✅ CLOSED: {closed_trades}

📊 TP1:{tp1_hits} TP2:{tp2_hits} TP3:{tp3_hits}
TP1→BE:{tp1_to_be} TP2→TP1:{tp2_to_tp1}
❌ SL DIRECTOS:{sl_direct}
==============================
""")

    time.sleep(5)