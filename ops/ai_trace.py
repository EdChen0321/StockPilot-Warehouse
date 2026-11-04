"""Bounded local metadata journal; never persist prompt/evidence/error text."""
import json
import threading


class TraceJournal:
    def __init__(self, directory, max_bytes=256_000):
        self.path = directory / 'requests.jsonl'
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def append(self, record):
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(record, ensure_ascii=False) + '\n'
                if self.path.exists() and self.path.stat().st_size + len(line.encode('utf-8')) > self.max_bytes:
                    self.path.replace(self.path.with_suffix('.previous.jsonl'))
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(line)
            return True
        except OSError:
            return False
