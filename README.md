# Financial Portfolio Aggregator

A comprehensive Python-based portfolio tracking system that aggregates transaction data from multiple financial institutions, calculates holdings, cost basis, and generates detailed reports with an interactive dashboard.

## Features

- 📊 **Multi-source parsing**: Supports 9+ financial institutions (Schwab, Robinhood, Coinbase, Vanguard, USAA, Voya, Apple Savings)
- 💰 **Cost basis tracking**: FIFO method with tax lot tracking
- 📈 **Performance analytics**: Time-weighted returns, S&P 500 comparison, unrealized/realized gains
- 🔄 **Corporate actions**: Handles stock splits, mergers, spinoffs, and conversions
- 📉 **Historical tracking**: Portfolio value over time with per-account breakdown
- 💵 **Income tracking**: Dividends, interest, staking rewards by year
- 🌐 **Interactive dashboard**: Visualizations with Chart.js
- 🗄️ **Smart caching**: Price, split, and sector data caching to minimize API calls

## Installation

```bash
# Clone the repository
git clone https://github.com/akourk/fin.git
cd fin

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Basic Usage

1. Place your transaction CSV files in `data/input/`
2. Run the aggregator:
   ```bash
   python detectAndClean.py
   ```
3. Open `dashboard/index.html` in a browser to view results

### Output Files

The tool generates the following reports in `data/output/`:
- `master_transactions.csv` - All transactions normalized
- `holdings.csv` - Current holdings
- `holdings_detail.csv` - Holdings with cost basis and gains
- `tax_lots.csv` - Individual tax lots with holding periods
- `realized_gains.csv` - Realized gains/losses from sales
- `income_report.csv` - Detailed income by symbol and year
- `income_by_year.csv` - Income summary for tax purposes
- `cash_balances.csv` - Cash account balances
- `account_summary.csv` - Summary metrics per account
- `historical_holdings.csv` - Portfolio value over time

## Supported Financial Institutions

| Institution | Account Types | Notes |
|------------|---------------|-------|
| Schwab | Brokerage, IRA, Roth IRA | Full support |
| Robinhood | Brokerage | Includes options trading |
| Coinbase | Crypto | Regular Coinbase |
| Coinbase Pro | Crypto | Pro/Advanced trading |
| Vanguard | 401(k) | SF 401k format |
| USAA | IRA | Victory Capital format |
| Voya | 401(k) | Atos 401k format |
| Apple | Savings | Apple Card Savings |
| Custom | Any | Pre-normalized format |

## Project Structure

```
fin/
├── detectAndClean.py          # Main entry point
├── fin/                        # Core package
│   ├── main.py                # Main processing logic
│   ├── config.py              # Configuration and mappings
│   ├── parsers/               # Institution-specific parsers
│   │   ├── base.py           # Common parser utilities
│   │   ├── registry.py       # Parser registration
│   │   ├── schwab.py
│   │   ├── robinhood.py
│   │   ├── coinbase.py
│   │   └── ...
│   ├── calculators/           # Financial calculations
│   │   ├── holdings.py       # Current holdings
│   │   ├── cost_basis.py     # FIFO cost basis
│   │   ├── historical.py     # Time-weighted returns
│   │   ├── income.py         # Income tracking
│   │   └── cash.py           # Cash balances
│   ├── reports/               # Report generation
│   │   ├── summaries.py      # Account/portfolio summaries
│   │   ├── exporters.py      # CSV/JS exports
│   │   └── retirement.py     # Retirement tracking
│   └── utils/                 # Utilities
│       ├── cache.py          # Cache management
│       ├── prices.py         # Price fetching (yfinance)
│       └── formatting.py     # Data formatting
├── data/
│   ├── input/                 # Place CSV files here
│   ├── output/                # Generated reports
│   ├── price_cache.json      # Cached price data
│   ├── split_cache.json      # Cached split data
│   └── sector_cache.json     # Cached sector data
└── dashboard/                 # Interactive dashboard
    ├── index.html
    ├── dashboard.js
    ├── styles.css
    └── data/                  # JS data files
```

## Configuration

Edit `fin/config.py` to customize:
- Account merge mappings (for rollovers/transfers)
- Symbol mappings (fund names to tickers)
- Delisted symbols
- IRA/401k contribution limits

## Data Privacy

⚠️ **Important**: This tool processes sensitive financial data locally. 
- All data stays on your machine
- The only external API calls are to yfinance for price data
- Add `data/input/*` and `data/output/*` to `.gitignore` (already configured)
- Never commit actual transaction files to version control

## Development

### Adding a New Parser

1. Create a new parser in `fin/parsers/your_institution.py`
2. Implement `parse_your_institution(file_path)` returning a DataFrame with unified columns
3. Register in `fin/parsers/registry.py`
4. Add detection logic to `detect_source()` function

### Running Tests

```bash
# Tests not yet implemented
# TODO: Add pytest test suite
```

## Technical Details

### Cost Basis Calculation
Uses FIFO (First In, First Out) method:
- Tracks individual tax lots with purchase dates
- Distinguishes long-term (>1 year) vs short-term holdings
- Handles complex scenarios: mergers, splits, spinoffs

### Time-Weighted Return (TWR)
Measures investment performance independent of cash flow timing:
- Breaks periods at each cash flow
- Calculates sub-period returns
- Geometrically links returns for true performance

### Stock Split Adjustment
Automatically adjusts historical transactions:
- Fetches split data from yfinance
- Applies cumulative multipliers to quantities/prices
- Prevents double-counting split shares

## Known Limitations

- Options positions tracked separately (not included in holdings count)
- Some delisted stocks may not have price data
- Crypto prices require `-USD` suffix for yfinance
- Performance depends on yfinance API availability

## Contributing

Contributions welcome! Areas for improvement:
- Unit tests
- Additional parser support
- Enhanced dashboard features
- Performance optimizations

## License

MIT License - See LICENSE file for details

## Disclaimer

This tool is for personal financial tracking only. Not financial advice. Verify all calculations independently for tax/reporting purposes.
