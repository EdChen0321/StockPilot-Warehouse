"""Local document numbers, allocated within the caller's write transaction."""
from datetime import datetime, timezone, timedelta
from secrets import randbelow
from .store import ConflictError


def document_number(db):
    prefix=datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')
    for _ in range(100):
        candidate=f'{prefix}{randbelow(1000000):06d}'
        exists=db.execute('''SELECT 1 FROM receipts WHERE number=? UNION ALL
            SELECT 1 FROM shipments WHERE number=? UNION ALL
            SELECT 1 FROM current_orders WHERE order_id=? LIMIT 1''',(candidate,candidate,candidate)).fetchone()
        if not exists:
            return candidate
    raise ConflictError('暫時無法產生不重複單號，請以相同操作重試')
