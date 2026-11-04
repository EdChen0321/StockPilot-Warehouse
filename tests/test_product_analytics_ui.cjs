// Rendering/interaction checks only; not a substitute for browser acceptance.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=String(text);this.children=[];this.value='';this.style={setProperty(key,value){this[key]=value;}};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this[key]=value;}
 get options(){return this.children;}
 querySelector(tag){return all(this).find(n=>n!==this&&n.tag===tag);}
}
function all(el){return [el,...el.children.flatMap(all)];}
function text(el){return all(el).map(n=>n.textContent).join(' ');}
const root=new Element('section'),calls=[],routes=[];
const data={start:'2026-10-01',end:'2026-10-02',days:2,as_of:'2026-10-03T00:00:00Z',data_complete:true,
 inventory:[{on_hand:3,available:2,coverage_days:'4',coverage_status:'可計算'}],
 daily:[{day:'2026-10-01',received:4}],daily_stock:[{day:'2026-10-01',on_hand:3,change:3},{day:'2026-10-02',on_hand:3,change:0}],
 unit_records:[{day:'2026-10-01',time:'2026-10-01T00:00:00Z',kind:'ship',unit_id:'unit1',shipment_id:'ship1',channel:'網店',number:'OUT1'}]};
let fail=false,release;
const context=vm.createContext({Node:Element,URLSearchParams,Date,Intl,Map,Set,
 window:{addEventListener(){},removeEventListener(){}},
 node:(tag,value)=>new Element(tag,value===undefined?'':value),$:()=>root,
 statisticsDate:()=> '2026-10-03',dateTime:v=>v,
 Option:function(label,value){return Object.assign(new Element('option',label),{value});},
 emptyRow:(body,_count,message)=>body.append(new Element('tr',message)),navigatePage:path=>routes.push(path),
 request:async path=>{calls.push(path);if(fail)throw new Error('讀取失敗');if(release)await release;
  return path.startsWith('/api/catalogue/')?{id:'p1',name:'合成商品',sku:'SKU1',kind:'product'}:data;}
});
// Use the shared fact-table renderer so grouping and row labels are exercised.
const stockExtras=fs.readFileSync('web/stock-extras.js','utf8');
vm.runInContext(stockExtras.slice(stockExtras.indexOf('function stockInfoTable('),stockExtras.indexOf('function stockDetailOverview(')),context);
vm.runInContext(fs.readFileSync('web/product-analytics.js','utf8'),context);
const query=encodeURIComponent('start=2026-10-01&end=2026-10-02');
(async()=>{
 await context.renderProductAnalytics(['product-analytics','p1',query]);
 assert.ok(calls.some(p=>p.includes('product_id=p1')));assert.match(text(root),/合成商品 · SKU1/);
 assert.match(text(root),/平均每日銷量\s+0.5/);assert.match(text(root),/目前在庫\s+3 件/);
 assert.match(text(root),/期間銷售/);assert.match(text(root),/目前庫存（全部通路）/);
 assert.equal(all(root).filter(n=>n.tag==='meter').length,0);
 const chart=root.querySelector('figure');assert.ok(chart);assert.equal(chart['aria-label'],'銷量圖，單位：件');
 const bars=all(chart).filter(n=>n.tag==='a');
 assert.equal(bars.length,2);assert.match(bars[0].textContent,/1 件/);assert.match(bars[1].textContent,/0 件/);
 assert.ok(bars[0].href.endsWith('/2026-10-01'));
 assert.ok(all(root).some(n=>n.tag==='th'&&n.scope==='row'&&n.textContent==='期間銷量（完成出貨）'));
 const day=all(root).find(n=>n.textContent==='2026-10-01');assert.ok(day.href.endsWith('/2026-10-01'));
 const form=root.querySelector('form');form.onsubmit({preventDefault(){}});assert.ok(routes[0].includes(query));
 const grain=chart.querySelector('select');grain.value='week';grain.onchange();assert.equal(all(chart).filter(n=>n.tag==='a').length,1);
 assert.ok(all(chart).find(n=>n.tag==='a').href.endsWith('/2026-10-01/2026-10-02'));
 await context.renderProductAnalytics(['product-analytics','p1',query,'2026-10-01','2026-10-02']);
 assert.ok(all(root).some(n=>n.href==='#shipping/ship1'));
 await context.renderProductAnalytics(['product-analytics','p1',query,'2026-10-01']);
 assert.ok(all(root).some(n=>n.href==='#shipping/ship1'));assert.ok(all(root).some(n=>n.href==='#quality/unit1'));
 assert.ok(all(root).some(n=>n.textContent==='返回每日數據'&&n.href.includes(query)));
 const filtered=encodeURIComponent('start=2026-10-01&end=2026-10-02&channel='+encodeURIComponent(JSON.stringify('其他')));
 await context.renderProductAnalytics(['product-analytics','p1',filtered,'2026-10-01']);
 assert.match(text(root),/期間銷量（完成出貨）\s+0 件/);assert.match(text(root),/目前在庫\s+3 件/);
 assert.match(text(root),/出貨通路\s+其他/);
 assert.match(text(root),/此條件沒有出貨/);
 data.data_complete=false;data.inventory[0].coverage_days=null;data.inventory[0].coverage_status='資料不足';
 await context.renderProductAnalytics(['product-analytics','p1',query]);assert.match(text(root),/平均每日銷量\s+資料不足/);
 const savedInventory=data.inventory;data.inventory=[];
 await context.renderProductAnalytics(['product-analytics','p1',query]);assert.match(text(root),/目前在庫\s+資料不足/);assert.doesNotMatch(text(root),/undefined 件/);
 data.inventory=savedInventory;
 fail=true;await context.renderProductAnalytics(['product-analytics','p1',query]);assert.match(text(root),/讀取失敗/);assert.doesNotMatch(text(root),/目前在庫/);
 fail=false;let resolve;release=new Promise(r=>resolve=r);const older=context.renderProductAnalytics(['product-analytics','old',query]);
 release=null;await context.renderProductAnalytics(['product-analytics','new',query]);const latest=text(root);resolve();await older;assert.equal(text(root),latest);
 console.log('Product analytics: SKU scope, filters, drilldown, back route, empty/error/incomplete states and request race passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
