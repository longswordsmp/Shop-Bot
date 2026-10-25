"""Synchronous data-access functions. Each takes a ``sqlite3.Connection`` as its first argument and is
executed on the database thread via ``Database.run`` or ``Database.transaction``."""
