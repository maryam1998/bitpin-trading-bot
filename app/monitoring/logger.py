import logging
import sys

def setup_logging(level=logging.INFO):
    root = logging.getLogger()
    root.setLevel(level)
    
    # حذف هندلرهای قبلی
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)
    
    # مخفی کردن لاگ‌های کتابخانه‌های خارجی
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
