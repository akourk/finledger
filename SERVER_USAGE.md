# Dashboard Server Usage

The dashboard now has two modes of operation:

## Local File Mode (Simple - Recommended for quick viewing)
Simply open `dashboard/index.html` in your browser. The refresh button will reload the page but **won't fetch new data**.

To update data, manually run in a terminal:
```powershell
.\.venv\Scripts\python.exe detectAndClean.py
```

Then refresh the browser page to see the updated data.

## Server Mode (Advanced - With Live Updates)

Server mode provides additional features:
- **Live Tab** - Real-time price updates and portfolio value
- **Working Refresh Button** - Click to refresh data without leaving the dashboard
- **Server-Sent Events** - Get live notifications when data is refreshed

### Starting the Server

1. Open a terminal/PowerShell in the project directory
2. Run:
   ```powershell
   .\.venv\Scripts\python.exe server.py
   ```
3. Open your browser to: **http://localhost:5000**

### Live Tab Features

When running in server mode, a **Live** tab appears with:

- **Server Connection Status** - Shows if you're connected to the live updates stream
- **Live Portfolio Value** - Click to get real-time portfolio value using current market prices
- **Quick Price Check** - Enter any symbol to get its current price
- **Activity Log** - See real-time events (refreshes, price checks, errors)
- **Top Holdings Live Prices** - View live prices for your top 10 holdings

### How It Works

The dashboard automatically detects whether it's running from:
- `file://` protocol (local file mode) - Live tab is hidden
- `http://` or `https://` protocol (server mode) - Live tab is shown

### API Endpoints

The server provides these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/status` | GET | Get last data generation timestamp |
| `/api/refresh` | POST | Trigger data refresh |
| `/api/health` | GET | Server health check |
| `/api/events` | GET | Server-Sent Events stream |
| `/api/live/price/<symbol>` | GET | Get live price for a symbol |
| `/api/live/portfolio-value` | GET | Get live portfolio value |

### Troubleshooting

If the refresh button shows "Error refreshing data":

**Option 1 - Manual Refresh (Recommended)**
1. Keep the server running
2. In a separate terminal, run: `.\.venv\Scripts\python.exe detectAndClean.py`
3. The dashboard will auto-reload and show fresh data

**Option 2 - Stop Server and Run Manually**
1. Press Ctrl+C to stop the server
2. Run: `.\.venv\Scripts\python.exe detectAndClean.py`
3. Restart server: `.\.venv\Scripts\python.exe server.py`
4. Reload browser

### Stopping the Server

Press `Ctrl+C` in the terminal to stop the server.
