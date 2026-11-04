"""Load packaged query guidance; routing never grants or removes tool permission."""
from pathlib import Path
import time


class GuidanceCache:
    """Per-service static text only. No questions, permissions or business data."""
    def __init__(self, ttl=60):
        self.ttl = ttl
        self.entries = {}
        self.hits = self.misses = 0

    def read(self, path):
        path = path.resolve()
        stat = path.stat()  # Missing files must fail even when previously cached.
        version = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        now = time.monotonic()
        entry = self.entries.get(path)
        if entry and entry[0] == version and now < entry[1]:
            self.hits += 1
            return entry[2]
        source = path.read_text(encoding='utf-8')
        if len(self.entries) >= 3 and path not in self.entries:
            self.entries.clear()
        self.entries[path] = (version, now + self.ttl, source)
        self.misses += 1
        return source

SKILL_ROOT = Path(__file__).with_name('query_skills')
SKILLS = (
    ('orders', 'order-comparison', ('訂單', '營收', '銷售', '賣', 'utm', 'order', 'sales', 'revenue')),
    ('shipping', 'shipping-cost', ('出貨', '實退', '退貨', '成本', 'shipping', 'shipment', 'cost', 'return')),
    ('inventory', 'inventory-overview', ('庫存', '商品', 'sku', '入庫', '出貨單', '櫃位', '調撥', '成本', '逐件', '異動', 'inventory', 'stock', 'product', 'receipt', 'location', 'transfer', 'cost')),
)


def load_query_skills(question, history, enabled_tools, cache=None):
    # Retain earlier context for follow-ups and mixed questions. This is only
    # instruction selection, not a claim to understand or execute the question.
    text = '\n'.join([question, *(message['text'] for message in history)]).casefold()
    available = [entry for entry in SKILLS if entry[0] in enabled_tools]
    selected = [entry for entry in available if any(word in text for word in entry[2])]
    if not selected:
        selected = available
    ids, bodies = [], []
    for tool, folder, _ in selected:
        try:
            path = SKILL_ROOT / folder / 'SKILL.md'
            source = cache.read(path) if cache else path.read_text(encoding='utf-8')
            body = source.split('---', 2)[2].strip()
        except (OSError, IndexError) as exc:
            raise ValueError('內建查詢配方無法載入，請檢查安裝檔案。') from exc
        ids.append(tool)
        bodies.append(body)
    return ids, '\n\n'.join(bodies)
