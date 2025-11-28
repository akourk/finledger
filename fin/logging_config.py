# Logging Configuration
# ====================
# Recommended improvements for better debugging and monitoring

import logging
from pathlib import Path

# Create logs directory
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Configure logging
def setup_logging(level=logging.INFO):
    """
    Set up logging configuration.
    
    Usage:
        from fin.config import setup_logging
        setup_logging(logging.DEBUG)  # For verbose output
    """
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_DIR / 'fin.log'),
            logging.StreamHandler()
        ]
    )
    
    # Suppress noisy loggers
    logging.getLogger('yfinance').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

# Example usage throughout codebase:
# logger = logging.getLogger(__name__)
# logger.info("Processing file: %s", file_path)
# logger.error("Failed to parse: %s", error)
