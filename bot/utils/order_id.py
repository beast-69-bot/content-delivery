"""
utils/order_id.py
Generates unique, sequential-looking order IDs.
"""

import random
import string
import time


_counter = int(time.time()) % 10000


def generate_order_id() -> str:
    global _counter
    _counter += 1
    suffix = "".join(random.choices(string.ascii_uppercase, k=2))
    return f"ORD{_counter:04d}{suffix}"
