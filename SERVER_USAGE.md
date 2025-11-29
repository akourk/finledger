# Dashboard Server Usage

The dashboard now has two modes of operation:

## Local File Mode (Simple - Recommended for now)
Simply open `dashboard/index.html` in your browser. The refresh button will reload the page but **won't fetch new data**.

To update data, manually run in a terminal:
```powershell
.\.venv\Scripts\python.exe detectAndClean.py
```

Then refresh the browser page to see the updated data.

## Server Mode (Advanced - Under Development)

The server mode is designed to allow the refresh button to actually fetch fresh price data, but it currently has some issues with the subprocess execution on Windows.

### If You Want to Try Server Mode

1. Open a terminal/PowerShell in the project directory
2. Run:
   ```powershell
   .\.venv\Scripts\python.exe server.py
   ```
3. Open your browser to: **http://localhost:5000**

The server will serve the dashboard, but clicking the refresh button may not work correctly due to subprocess issues.

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

### Why the Refresh Button May Not Work

The server tries to spawn a subprocess to run `detectAndClean.py`, but this can fail on Windows due to:
- Virtual environment path issues
- Permission restrictions
- Shell execution contexts

###  Future Improvements

The refresh feature needs better Windows subprocess handling. For now, manually running the script in a separate terminal is the most reliable approach.

### Stopping the Server

Press `Ctrl+C` in the terminal to stop the server.
