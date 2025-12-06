"""
Dashboard Server
================
Lightweight Flask server for the portfolio dashboard.
Provides endpoints for:
- Serving the dashboard
- Triggering data refresh
- Server-Sent Events for live updates
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="dashboard", static_url_path="")
CORS(app)  # Enable CORS for local development

# Get the project root directory
ROOT_DIR = Path(__file__).parent
PYTHON_EXECUTABLE = sys.executable
SCRIPT_PATH = ROOT_DIR / "detectAndClean.py"

# Message queue for SSE (Server-Sent Events)
# Each connected client gets messages from this
message_queues = []
message_lock = threading.Lock()


def broadcast_message(event_type, data):
    """Send a message to all connected SSE clients."""
    message = {"type": event_type, "data": data, "timestamp": datetime.now().isoformat()}
    with message_lock:
        for q in message_queues:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass  # Skip if queue is full


@app.route("/")
def index():
    """Serve the dashboard index page."""
    return send_from_directory("dashboard", "index.html")


@app.route("/api/refresh", methods=["POST"])
def refresh_data():
    """
    Trigger data refresh by running detectAndClean.py main function.

    Returns:
        JSON with status and any error messages
    """
    try:
        print(f"\n{'='*60}")
        print(f"REFRESH TRIGGERED at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        # Broadcast refresh started
        broadcast_message("refresh_started", {"message": "Data refresh started..."})

        # Import and run the main function directly
        import sys

        # Temporarily redirect stdout to capture output
        from io import StringIO

        from fin.main import main

        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()

        try:
            # Call the main function directly
            main()

            # Restore stdout
            sys.stdout = old_stdout
            output = captured_output.getvalue()

            print(f"\n{'='*60}")
            print(f"REFRESH COMPLETED SUCCESSFULLY")
            print(f"{'='*60}\n")

            # Show last few lines of output for debugging
            output_lines = output.strip().split("\n")
            if output_lines:
                print("Last output lines:")
                for line in output_lines[-5:]:
                    print(f"  {line}")

            # Broadcast refresh completed
            broadcast_message(
                "refresh_completed",
                {
                    "message": "Data refresh completed successfully",
                    "output_lines": output_lines[-10:] if output_lines else [],
                },
            )

            return jsonify(
                {
                    "status": "success",
                    "message": "Data refreshed successfully",
                    "timestamp": datetime.now().isoformat(),
                    "output_preview": (
                        "\n".join(output_lines[-10:]) if output_lines else "Refresh completed"
                    ),
                }
            )

        except Exception as exec_error:
            # Restore stdout even if there's an error
            sys.stdout = old_stdout
            print(f"ERROR during script execution: {str(exec_error)}")
            import traceback

            traceback.print_exc()

            # Broadcast error
            broadcast_message(
                "refresh_error", {"message": f"Refresh failed: {str(exec_error)}"}
            )

            return (
                jsonify(
                    {
                        "status": "error",
                        "message": f"Script execution error: {str(exec_error)}",
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
                500,
            )

    except Exception as e:
        print(f"ERROR: Exception during refresh: {str(e)}")
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {"status": "error", "message": str(e), "timestamp": datetime.now().isoformat()}
            ),
            500,
        )


@app.route("/api/status")
def status():
    """
    Get the last generation time from portfolio_summary.js.

    Returns:
        JSON with generation timestamp
    """
    try:
        summary_path = ROOT_DIR / "dashboard" / "data" / "portfolio_summary.js"

        if not summary_path.exists():
            return jsonify({"status": "not_generated", "message": "Data not yet generated"})

        # Read the file and extract GeneratedAt
        with open(summary_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Simple extraction of GeneratedAt field
        if '"GeneratedAt":' in content:
            start = content.index('"GeneratedAt":') + len('"GeneratedAt":')
            end = content.index(",", start)
            timestamp_str = content[start:end].strip().strip('"')

            return jsonify(
                {
                    "status": "success",
                    "generated_at": timestamp_str,
                    "file_modified": datetime.fromtimestamp(
                        summary_path.stat().st_mtime
                    ).isoformat(),
                }
            )
        else:
            return jsonify({"status": "unknown", "message": "Could not find generation timestamp"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


@app.route("/api/events")
def events():
    """
    Server-Sent Events endpoint for live updates.
    Clients can connect to this to receive real-time notifications.
    """

    def event_stream():
        # Create a queue for this client
        q = queue.Queue(maxsize=100)
        with message_lock:
            message_queues.append(q)

        try:
            # Send initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now().isoformat()})}\n\n"

            while True:
                try:
                    # Wait for messages with timeout
                    message = q.get(timeout=30)
                    yield f"data: {json.dumps(message)}\n\n"
                except queue.Empty:
                    # Send heartbeat to keep connection alive
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
        except GeneratorExit:
            pass
        finally:
            # Remove queue when client disconnects
            with message_lock:
                if q in message_queues:
                    message_queues.remove(q)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/live/market-status")
def market_status():
    """
    Get current market status (open, closed, pre-market, after-hours).
    Based on US Eastern time and NYSE/NASDAQ hours.
    """
    try:
        from datetime import timezone
        import pytz

        # Get current time in Eastern timezone
        eastern = pytz.timezone("US/Eastern")
        now = datetime.now(eastern)

        # Market hours (Eastern Time)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        pre_market_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
        after_hours_end = now.replace(hour=20, minute=0, second=0, microsecond=0)

        # Check if weekend
        is_weekend = now.weekday() >= 5  # Saturday = 5, Sunday = 6

        # US Market holidays for 2025 (simplified list)
        holidays_2025 = [
            "2025-01-01",  # New Year's Day
            "2025-01-20",  # MLK Day
            "2025-02-17",  # Presidents Day
            "2025-04-18",  # Good Friday
            "2025-05-26",  # Memorial Day
            "2025-06-19",  # Juneteenth
            "2025-07-04",  # Independence Day
            "2025-09-01",  # Labor Day
            "2025-11-27",  # Thanksgiving
            "2025-12-25",  # Christmas
        ]
        is_holiday = now.strftime("%Y-%m-%d") in holidays_2025

        if is_weekend or is_holiday:
            status = "closed"
            status_text = "Market Closed" + (" (Weekend)" if is_weekend else " (Holiday)")
            next_open = "Monday" if is_weekend else "Next trading day"
        elif now < pre_market_start:
            status = "closed"
            status_text = "Market Closed"
            next_open = "Pre-market at 4:00 AM ET"
        elif now < market_open:
            status = "pre-market"
            status_text = "Pre-Market"
            next_open = f"Opens at 9:30 AM ET"
        elif now < market_close:
            status = "open"
            status_text = "Market Open"
            closes_in = (market_close - now).seconds // 60
            hours = closes_in // 60
            mins = closes_in % 60
            next_open = f"Closes in {hours}h {mins}m"
        elif now < after_hours_end:
            status = "after-hours"
            status_text = "After Hours"
            next_open = "Regular trading resumes 9:30 AM ET"
        else:
            status = "closed"
            status_text = "Market Closed"
            next_open = "Pre-market at 4:00 AM ET"

        return jsonify(
            {
                "status": "success",
                "market_status": status,
                "status_text": status_text,
                "next_open": next_open,
                "current_time": now.strftime("%I:%M %p ET"),
                "is_trading_hours": status == "open",
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/live/day-gain")
def day_gain():
    """
    Calculate today's gain/loss for the portfolio.
    Compares current prices vs previous close for all holdings.
    """
    try:
        import yfinance as yf

        # Load holdings
        holdings_path = ROOT_DIR / "dashboard" / "data" / "holdings_detail.js"
        if not holdings_path.exists():
            return jsonify({"status": "error", "message": "Holdings data not found"}), 404

        with open(holdings_path, "r", encoding="utf-8") as f:
            content = f.read()

        start = content.index("[")
        end = content.rindex("]") + 1
        holdings = json.loads(content[start:end])

        total_current = 0
        total_prev_close = 0
        holding_changes = []

        for holding in holdings:
            symbol = holding.get("Symbol", "")
            quantity = holding.get("Quantity", 0)

            if not symbol or quantity == 0:
                continue

            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                price = info.get("regularMarketPrice") or info.get("currentPrice")
                prev_close = info.get("previousClose") or info.get(
                    "regularMarketPreviousClose"
                )

                if price and prev_close:
                    current_value = price * quantity
                    prev_value = prev_close * quantity
                    total_current += current_value
                    total_prev_close += prev_value

                    change = price - prev_close
                    change_pct = (change / prev_close) * 100 if prev_close > 0 else 0
                    value_change = current_value - prev_value

                    holding_changes.append(
                        {
                            "symbol": symbol,
                            "price": round(price, 2),
                            "prev_close": round(prev_close, 2),
                            "change": round(change, 2),
                            "change_pct": round(change_pct, 2),
                            "value_change": round(value_change, 2),
                            "quantity": quantity,
                        }
                    )
            except Exception:
                pass

        # Calculate totals
        day_change = total_current - total_prev_close
        day_change_pct = (
            (day_change / total_prev_close) * 100 if total_prev_close > 0 else 0
        )

        # Sort by absolute value change to find top movers
        holding_changes.sort(key=lambda x: abs(x["value_change"]), reverse=True)

        return jsonify(
            {
                "status": "success",
                "current_value": round(total_current, 2),
                "prev_close_value": round(total_prev_close, 2),
                "day_change": round(day_change, 2),
                "day_change_pct": round(day_change_pct, 2),
                "top_movers": holding_changes[:5],
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/live/intraday/<symbol>")
def intraday_data(symbol):
    """
    Get intraday price data for sparkline charts.
    Returns price points for today.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol.upper())
        # Get 1-day data with 5-minute intervals
        hist = ticker.history(period="1d", interval="5m")

        if hist.empty:
            return jsonify({"status": "error", "message": f"No intraday data for {symbol}"}), 404

        # Extract close prices
        prices = []
        for idx, row in hist.iterrows():
            prices.append({
                "time": idx.strftime("%H:%M"),
                "price": round(row["Close"], 2)
            })

        # Get current price and prev close for reference
        info = ticker.info
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")

        return jsonify({
            "status": "success",
            "symbol": symbol.upper(),
            "prices": prices,
            "prev_close": round(prev_close, 2) if prev_close else None,
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/live/price/<symbol>")
def live_price(symbol):
    """
    Get current price for a symbol.
    Useful for live price updates.
    Handles mutual funds which only have EOD pricing.
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol.upper())
        info = ticker.info

        # Check if this is a mutual fund (typically ends in X, or has specific quoteType)
        quote_type = info.get("quoteType", "").upper()
        is_mutual_fund = quote_type == "MUTUALFUND" or (
            symbol.upper().endswith("X") and quote_type not in ["ETF", "EQUITY"]
        )

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        nav = info.get("navPrice")  # Net Asset Value for mutual funds

        # For mutual funds, use NAV if available, otherwise previous close
        if is_mutual_fund:
            if nav:
                price = nav
            elif not price and prev_close:
                price = prev_close

        if price:
            change = None
            change_pct = None
            if prev_close and prev_close > 0:
                change = price - prev_close
                change_pct = (change / prev_close) * 100

            return jsonify(
                {
                    "status": "success",
                    "symbol": symbol.upper(),
                    "price": round(price, 2),
                    "change": round(change, 2) if change else 0,
                    "change_pct": round(change_pct, 2) if change_pct else 0,
                    "is_mutual_fund": is_mutual_fund,
                    "price_type": "NAV" if is_mutual_fund else "Live",
                    "timestamp": datetime.now().isoformat(),
                }
            )
        else:
            return jsonify({"status": "error", "message": f"No price data for {symbol}"}), 404

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/live/portfolio-value")
def live_portfolio_value():
    """
    Calculate current portfolio value using live prices.
    Includes cash balances (like Apple Savings).
    """
    try:
        import yfinance as yf

        # Load holdings from the data file
        holdings_path = ROOT_DIR / "dashboard" / "data" / "holdings_detail.js"
        if not holdings_path.exists():
            return jsonify({"status": "error", "message": "Holdings data not found"}), 404

        with open(holdings_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract the array from the JS file
        start = content.index("[")
        end = content.rindex("]") + 1
        holdings = json.loads(content[start:end])

        # Load cash balances (Apple Savings, etc.)
        cash_balances_path = ROOT_DIR / "dashboard" / "data" / "cash_balances.js"
        cash_total = 0
        cash_accounts = []

        if cash_balances_path.exists():
            try:
                with open(cash_balances_path, "r", encoding="utf-8") as f:
                    cash_content = f.read()
                # Extract the array from the JS file
                cash_start = cash_content.index("[")
                cash_end = cash_content.rindex("]") + 1
                cash_data = json.loads(cash_content[cash_start:cash_end])

                for cash_account in cash_data:
                    # Field is "CurrentBalance" not "Balance"
                    balance = cash_account.get("CurrentBalance", 0)
                    account_name = cash_account.get("Account", "Cash")
                    if balance > 0:
                        cash_total += balance
                        cash_accounts.append(
                            {"account": account_name, "balance": round(balance, 2)}
                        )
            except Exception as e:
                print(f"Warning: Could not load cash balances: {e}")

        total_value = cash_total  # Start with cash
        updated_holdings = []

        for holding in holdings:
            symbol = holding.get("Symbol", "")
            quantity = holding.get("Quantity", 0)

            if not symbol or quantity == 0:
                continue

            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info
                price = info.get("regularMarketPrice") or info.get("currentPrice")

                if price:
                    value = price * quantity
                    total_value += value
                    updated_holdings.append(
                        {
                            "symbol": symbol,
                            "quantity": quantity,
                            "price": round(price, 2),
                            "value": round(value, 2),
                        }
                    )
            except Exception:
                # Skip symbols that fail
                pass

        return jsonify(
            {
                "status": "success",
                "total_value": round(total_value, 2),
                "holdings_count": len(updated_holdings),
                "cash_total": round(cash_total, 2),
                "cash_accounts": cash_accounts,
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/favicon.ico")
def favicon():
    """Return empty response for favicon to avoid 404s."""
    return "", 204


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("Portfolio Dashboard Server")
    print(f"{'='*60}")
    print(f"Root directory: {ROOT_DIR}")
    print(f"Python executable: {PYTHON_EXECUTABLE}")
    print(f"Script path: {SCRIPT_PATH}")
    print(f"\nStarting server at http://localhost:5000")
    print("Press Ctrl+C to stop")
    print(f"{'='*60}\n")

    debug_mode = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    app.run(host="localhost", port=5000, debug=debug_mode)
