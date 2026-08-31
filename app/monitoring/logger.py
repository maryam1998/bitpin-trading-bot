import logging
import sys
import re

_SECRET_PATTERN = re.compile(r"(api[_-]?key|api[_-]?secret|token)\s*[:=]\s*\S+", re.IGNORECASE)


class SecretRedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = _SECRET_PATTERN.sub(r"\1=***REDACTED***", str(record.msg))
        except Exception:
            pass
        return True


def setup_logging(log_file: str = "logs/trading_bot.log", level=logging.INFO):
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.addFilter(SecretRedactingFilter())
    root.addHandler(stream)

    try:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(fmt)
        file_handler.addFilter(SecretRedactingFilter())
        root.addHandler(file_handler)
    except OSError:
        pass

    return root
