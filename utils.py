"""Utility functions for Reachy Mini Motor."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def get_logger(name: str) -> logging.Logger:
    """Get a logger with the standard format."""
    return logging.getLogger(name)
