import random, string
from typing import Tuple

def generate_code(prefix: str = "", length: int = 6) -> str:
    core = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
    return f"{prefix}{core}"

def age_bucket(age: int) -> str:
    try:
        a = int(age)
    except Exception:
        return "Unknown"
    if 7 <= a <= 9:
        return "7-9"
    if 10 <= a <= 12:
        return "10-12"
    if 13 <= a <= 15:
        return "13-15"
    if 16 <= a <= 18:
        return "16-18"
    if a >= 19:
        return "18+"
    return "Unknown"
