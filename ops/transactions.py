"""Shared atomic command boundary; never a replacement for authenticated identity."""
import hashlib
import json
import sqlite3

from .store import ConflictError, encode, required_text


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS operations(
        id TEXT PRIMARY KEY, action TEXT NOT NULL, request_hash TEXT NOT NULL, response TEXT NOT NULL)''')


def command(store, action, body, apply):
    try:
        return _command(store, action, body, apply)
    except (ValueError, KeyError, sqlite3.Error) as exc:
        # The business transaction has rolled back before recording the rejection.
        # If storage itself is unavailable, preserve the original failure.
        try:
            with store.connect() as db:
                target = body.get('operation_id', 'invalid-operation') if isinstance(body, dict) else 'invalid-operation'
                store.event(db, action + '_rejected', str(target)[:100], {'reason': str(exc)[:500]})
        except sqlite3.Error:
            pass
        raise


def _command(store, action, body, apply):
    if not isinstance(body, dict):
        raise ValueError('操作內容必須為物件')
    operation_id = required_text(body.get('operation_id'), '操作識別', 100)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
        if previous:
            if previous['action'] != action or previous['request_hash'] != digest:
                raise ConflictError('相同操作識別已有不同內容，請重新整理後再操作')
            store.event(db, action + '_retry', operation_id, {'operation_id': operation_id})
            return json.loads(previous['response'])
        result = apply(db)
        store.event(db, action, result.get('id', operation_id), {'operation_id': operation_id, 'result': result})
        db.execute('INSERT INTO operations VALUES(?,?,?,?)', (operation_id, action, digest, encode(result)))
        return result
