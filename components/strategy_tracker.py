import os
import psycopg2
import json
import pandas as pd
import requests
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine
from datetime import timedelta
from time import sleep
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import requests
import os
load_dotenv()



models_to_notify = ["LSTMModel"]

@retry(
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True
)

def send_telegram(msg: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}  
    response = requests.post(url, json=data)
    if response.status_code == 429:
        retry_after = response.json().get("parameters", {}).get("retry_after", 1)
        print(f"⚠️ Rate limited. Sleeping for {retry_after}s...")
        sleep(retry_after)
        raise requests.exceptions.RequestException("Rate limit hit")

    response.raise_for_status()
    return response


def format_closure_message(outcome: str, coin: str, model: str, entry: float, exit: float, profit: float, closed_at: datetime) -> str:
    emojis = {
        "take_profit": "🎯",
        "stop_loss": "💥",
        "timeout": "⏳",
    }
    emoji = emojis.get(outcome, "📉")
    pct = (exit - entry) / entry * 100

    return (
        f"{emoji} *Signal Closed: {outcome.replace('_', ' ').title()}*\n"
        f"🪙 Coin: {coin}\n📊 Model: {model}\n"
        f"💰 Entry: {entry:.4f}\n"
        f"💸 Exit: {exit:.4f}\n"
        f"📊 PnL: {profit:+.4f} ({pct:+.2f}%)\n"
        f"⏱️ Closed: {closed_at}"
    )

def handle_closure(conn, signal_id, coin, model, created_at, entry, exit_price, closed_at, outcome , profit):
    """
    Handles the full lifecycle of a closed signal:
    - Calculates profit
    - Records the closure in `closed_signals`
    - Updates the `strategy_signals` table
    - Sends a formatted message via Telegram
    """
    profit = float(profit)
    record_closed_signal(conn, signal_id, coin, model, created_at, exit_price, closed_at, entry, outcome , profit)
    mark_signal_closed(conn, signal_id, entry, exit_price, closed_at, outcome)
    msg = format_closure_message(outcome, coin, model, entry, exit_price, profit, closed_at)
    send_telegram(msg)
    sleep(1)

def get_stored_klines(coin: str, start: str, end: str, interval: str = "1h") -> pd.DataFrame:
    start_ts = pd.to_datetime(start)
    end_ts = pd.to_datetime(end)

    conn = psycopg2.connect(
        dbname="crypto_predictions",
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
    )

    engine = create_engine(f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/crypto_predictions")

    query = """
        SELECT open_time, close
        FROM binance_klines
        WHERE symbol = %s
        AND timeframe = %s
        AND open_time >= %s
        AND open_time <= %s
        ORDER BY open_time ASC
    """

    df = pd.read_sql_query(query, engine, params=(coin, interval, start_ts, end_ts))

    df["open_time"] = pd.to_datetime(df["open_time"])
    df["close"] = df["close"].astype(float)

    conn.close()
    return df

def record_closed_signal(conn, signal_id, coin, model, created_at, exit_price, closed_at, entry, outcome , profit):
    cursor = conn.cursor()

    # Ensure all numeric values are native Python floats
    entry = float(entry)
    exit_price = float(exit_price)
    cursor.execute("""
        INSERT INTO closed_signals (id, coin, model_name, created_at, closed_at, entry, exit, profit, outcome)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """, (
        signal_id,
        coin,
        model,
        created_at,
        closed_at,
        entry,
        exit_price,
        profit,
        outcome
    ))

    conn.commit()
    cursor.close()

def mark_signal_closed(conn, signal_id, entry_price, exit_price, closed_at, outcome):
    cursor = conn.cursor()

    # Ensure native float types to avoid psycopg2 errors
    entry_price = float(entry_price)
    exit_price = float(exit_price)

    cursor.execute("""
        UPDATE strategy_signals
        SET
            entry_price = %s,
            exit_price = %s,
            closed_at = %s,
            outcome = %s,
            status = 'closed'
        WHERE id = %s
    """, (entry_price, exit_price, closed_at, outcome, signal_id))

    conn.commit()
    cursor.close()


def process_open_signals():
    conn = psycopg2.connect(
        dbname="crypto_predictions",
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
    )
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, coin, model_name, created_at, signal
        FROM strategy_signals
        WHERE
        signal->>'action' = 'BUY'
        AND (status IS NULL OR status = 'open')
            ORDER BY created_at ASC
        """)
    rows = cursor.fetchall()
    if not rows:
        print("No open signals.")
        return

    for row in rows:
        signal_id, coin, model, created_at, sig = row

        entry = float(sig["entry"])
        tp = float(sig["take_profit"])
        sl = float(sig["stop_loss"])
        end_time = created_at + timedelta(hours=12)    
        klines = get_stored_klines(coin, start=created_at.isoformat(), end=end_time.isoformat())

        for _, row in klines.iterrows():
            price = row["close"]
            
            if price >= tp:
                profit =  tp - entry
                outcome = "take_profit"
                handle_closure(conn, signal_id, coin, model, created_at, entry, price, row["open_time"], outcome , profit=profit)
            elif  price <= sl:
                profit =  price - entry
                outcome = "stop_loss"
                handle_closure(conn, signal_id, coin, model, created_at, entry, price, row["open_time"], outcome , profit=profit)
            else:
                continue

            print(f"""
                🔍 Signal Closed:
                Coin: {coin}
                Model: {model}
                Entry: {entry:.2f}
                Exit: {price:.2f}
                Profit: {profit:.2f}
                Outcome: {outcome.replace('_', ' ').title()}
                Closed at: {row['open_time']}
            """)
            break

        else:
            if not klines.empty:
                last_price = klines["close"].iloc[-1]
                last_time = klines["open_time"].iloc[-1]
                if datetime.utcnow() >= end_time:
                    profit =  last_price - entry
                    outcome = "timeout"
                    handle_closure(conn, signal_id, coin, model, created_at, entry, last_price, last_time, outcome , profit=profit)
                    print(f"""
                        🔍 Signal Closed:
                        Coin: {coin}
                        Model: {model}
                        Entry: {entry:.2f}
                        Exit: {last_price:.2f}
                        Profit: {profit:.2f}
                        Outcome: Timeout
                        Closed at: {last_time}
                    """)
                else:
                    print("⏳ Still within 12-hour window — not closing yet.")
            else:
                print("⚠️ No kline data found for this signal's window.")

    cursor.close()
    conn.close()
