# Contributing to Financial Portfolio Aggregator

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Development Setup

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/akourk/fin.git
   cd fin
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -e ".[dev]"  # Install with development dependencies
   ```

4. **Run tests**
   ```bash
   pytest tests/ -v
   ```

## Code Style

We use the following tools to maintain code quality:

- **Black** for code formatting (line length: 100)
- **isort** for import sorting
- **flake8** for linting
- **mypy** for type checking (optional but recommended)

Before committing, run:
```bash
black fin tests
isort fin tests
flake8 fin --max-line-length=119
mypy fin --ignore-missing-imports
```

## Adding a New Parser

To add support for a new financial institution:

1. **Create a new parser file**: `fin/parsers/your_institution.py`

2. **Implement the parser function**:
   ```python
   """
   Parser for Your Institution
   """
   import pandas as pd
   import pathlib
   from fin.config import UNIFIED_COLUMNS, ACTION_MAP
   
   def parse_your_institution(file_path: pathlib.Path) -> pd.DataFrame:
       """
       Parse transaction data from Your Institution.
       
       Expected CSV format:
       - Date, Type, Symbol, Shares, Price, Amount, etc.
       
       Returns DataFrame with unified columns.
       """
       df = pd.read_csv(file_path)
       
       # Map to unified format
       normalized = pd.DataFrame({
           'Date': pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d'),
           'Account': 'Your Institution Account',
           'Symbol': df['Symbol'].fillna(''),
           'Action': df['Type'].map(ACTION_MAP).fillna('Other'),
           'Quantity': df['Shares'].fillna(0),
           'Price': df['Price'].fillna(0),
           'Fee': df['Fee'].fillna(0),
           'Amount': df['Amount'].fillna(0),
           'Currency': 'USD',
           'Note': df['Description'].fillna(''),
           'Source': 'Your Institution'
       })
       
       return normalized[UNIFIED_COLUMNS]
   ```

3. **Register the parser**: Add to `fin/parsers/registry.py`
   ```python
   from fin.parsers.your_institution import parse_your_institution
   
   PARSER_REGISTRY = {
       # ... existing parsers
       "your_institution": parse_your_institution,
   }
   ```

4. **Add detection logic**: Update `detect_source()` in `registry.py`
   ```python
   def detect_source(file_path: pathlib.Path) -> Optional[str]:
       filename = file_path.name.lower()
       
       if "yourinstitution" in filename:
           return "your_institution"
       
       # ... rest of detection logic
   ```

5. **Write tests**: Create `tests/test_your_institution.py`

6. **Update documentation**: Add to README.md supported institutions table

## Testing Guidelines

- Write tests for all new functionality
- Aim for >80% code coverage
- Use pytest fixtures for common test data
- Mock external API calls (yfinance)

Example test structure:
```python
def test_parse_your_institution():
    """Test parsing of Your Institution CSV."""
    test_data = "Date,Type,Symbol,Shares,Price,Amount\n2024-01-01,Buy,AAPL,10,150.0,1500.0"
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(test_data)
        f.flush()
        result = parse_your_institution(Path(f.name))
    
    assert len(result) == 1
    assert result.loc[0, 'Symbol'] == 'AAPL'
    assert result.loc[0, 'Action'] == 'Buy'
```

## Pull Request Process

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes and commit with clear messages
3. Add/update tests
4. Ensure all tests pass: `pytest tests/`
5. Update documentation if needed
6. Push and create a pull request

### PR Checklist
- [ ] Tests pass
- [ ] Code follows style guidelines (Black, flake8)
- [ ] Documentation updated
- [ ] Commit messages are clear
- [ ] No sensitive data in commits

## Reporting Issues

When reporting bugs, please include:
- Operating system and Python version
- Steps to reproduce
- Expected vs actual behavior
- Relevant log output (redact sensitive data)
- Sample data (anonymized) if applicable

## Security

**Never commit sensitive financial data!**

- All transaction files should be in `data/input/` (gitignored)
- Redact account numbers and personal info in examples
- Don't include API keys or credentials
- Report security issues privately to maintainers

## Feature Requests

We welcome feature suggestions! Please:
- Check existing issues first
- Describe the use case
- Explain expected behavior
- Consider implementation approach

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Help others learn and grow
- Maintain confidentiality of financial data

## Questions?

Feel free to open an issue for:
- Questions about usage
- Clarification on implementation
- Discussion of new features
- Help with contributions

Thank you for contributing! 🚀
