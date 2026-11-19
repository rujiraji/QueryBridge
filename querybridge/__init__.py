"""Discover and query relational data with QueryBridge."""

from .connector import Connector, QueryLimits
from .sqlite import SQLiteConnector

__all__ = ["Connector", "QueryLimits", "SQLiteConnector"]
