from slowapi import Limiter
from slowapi.util import get_remote_address

# Only explicitly decorated endpoints are rate-limited; no blanket default.
limiter = Limiter(key_func=get_remote_address)
