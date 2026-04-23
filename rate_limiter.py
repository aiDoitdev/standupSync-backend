"""Shared slowapi Limiter instance — import this in main.py and any router that needs limits."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
