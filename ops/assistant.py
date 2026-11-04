"""Provider-free, read-only tool contract. This is not a live language model."""
from dataclasses import dataclass
from .catalogue import Catalogue
from .insights import product_overview
from .materials import Materials
from .sales import sales


@dataclass(frozen=True)
class ReadScope:
    domains: frozenset = frozenset({'catalogue','sales','stock','materials'})
    product_ids: frozenset | None = None
    account_ids: frozenset | None = None
    can_read_cost: bool = False


class ReadBroker:
    def __init__(self, store, scope):
        self.store, self.scope = store, scope

    def _domain(self, *names):
        if not set(names).issubset(self.scope.domains):
            raise PermissionError('沒有此查詢領域的讀取權限')

    def _scope(self, args, product=True, account=True):
        if product and self.scope.product_ids is not None and args.get('product_id') not in self.scope.product_ids:
            raise PermissionError('商品超出允許範圍；受限查詢須明確指定商品')
        if account and self.scope.account_ids is not None and args.get('account_id') not in self.scope.account_ids:
            raise PermissionError('訂單來源超出允許範圍；受限查詢須明確指定來源')

    def _sales(self, result):
        if self.scope.product_ids is not None:
            result['physical_quantities']=[x for x in result['physical_quantities'] if x['product_id'] in self.scope.product_ids]
        return result

    def invoke(self, tool, arguments):
        contracts={'find_products':{'q'},'product_overview':{'product_id','account_id','start','end'},
            'query_sales':{'product_id','account_id','start','end'},'material_summary':{'plan_ids'}}
        if not isinstance(tool,str) or tool not in contracts:
            raise ValueError('此工具未開放；僅允許固定唯讀查詢')
        if not isinstance(arguments,dict) or set(arguments)-contracts[tool]:
            raise ValueError('工具參數不正確，不接受SQL、命令或權限覆寫')
        for key,value in arguments.items():
            if key!='plan_ids' and (not isinstance(value,str) or len(value)>200):
                raise ValueError('查詢參數須為最多200字元文字')
        if tool=='find_products':
            self._domain('catalogue')
            products=Catalogue(self.store).list(arguments.get('q',''))['products']
            return dict(products=[{key:p[key] for key in ('id','sku','name','barcode','kind','active')}
                for p in products if self.scope.product_ids is None or p['id'] in self.scope.product_ids])
        if tool=='query_sales':
            self._domain('sales');self._scope(arguments)
            return self._sales(sales(self.store,**arguments))
        if tool=='product_overview':
            self._domain('catalogue','sales','stock','materials');self._scope(arguments)
            if not arguments.get('product_id'):
                raise ValueError('請先選定商品')
            data=product_overview(self.store,**arguments)
            data['product']={key:data['product'][key] for key in ('id','sku','name','barcode','kind','active')}
            data['sales']=self._sales(data['sales'])
            if data['stock']:
                fields=['id','sku','name','on_hand','allocated','available']
                if self.scope.can_read_cost:
                    fields.append('stock_cost_amount')
                data['stock']={key:data['stock'][key] for key in fields}
            return data
        self._domain('materials')
        ids=arguments.get('plan_ids')
        if ids is not None and (not isinstance(ids,list) or any(not isinstance(i,str) or len(i)>100 for i in ids) or len(ids)>500):
            raise ValueError('計畫篩選格式不正確')
        if self.scope.product_ids is not None:
            if not ids:
                raise PermissionError('受限查詢須明確指定生產計畫')
            plans=Materials(self.store).snapshot()['plans']
            allowed={p['id'] for p in plans if p['product_id'] in self.scope.product_ids}
            if not set(ids).issubset(allowed):
                raise PermissionError('計畫超出允許商品範圍')
        return Materials(self.store).summary(ids)


def preview(store, body, scope):
    if not isinstance(body,dict) or set(body)-{'question','calls'}:
        raise ValueError('預覽只接受問題文字與唯讀工具呼叫，不接受權限設定')
    question=body.get('question','')
    if not isinstance(question,str) or len(question)>2000:
        raise ValueError('問題文字最多2000字元')
    calls=body.get('calls')
    if not isinstance(calls,list) or not 1<=len(calls)<=4:
        raise ValueError('每次工具預覽須有1至4筆呼叫')
    broker=ReadBroker(store,scope)
    results=[]
    for call in calls:
        if not isinstance(call,dict) or set(call)!={'tool','arguments'}:
            raise ValueError('工具呼叫須明確包含tool與arguments')
        results.append(dict(tool=call['tool'],data=broker.invoke(call['tool'],call['arguments'])))
    return dict(mode='tool-preview',model_connected=False,results=results,
        notice='未接真實 AI；只預覽所選唯讀工具結果，不解讀問題或執行其中指令。',
        limits=dict(max_calls=4,automatic_retries=0,model_calls=0))
