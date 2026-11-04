// Real ECharts SVG rendering with a DOM adapter; this is not a browser layout test.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const echarts=require('../web/vendor/echarts-6.1.0/echarts.min.js');
let width=320;const charts=[],observers=[],events={};
class Element{
 constructor(tag,text='',className=''){Object.assign(this,{tag,textContent:String(text),className,children:[],isConnected:true,clientWidth:width,value:''});}
 append(...children){this.children.push(...children);if(this.tag==='select'&&!this.value)this.value=this.children[0]?.value||'';}
 replaceChildren(...children){this.children=children;}setAttribute(k,v){this[k]=v;}
}
const all=el=>[el,...el.children.flatMap(all)],text=el=>all(el).map(n=>n.textContent).join(' ');
const context=vm.createContext({Node:Element,URLSearchParams,Date,Intl,Map,Set,
 node:(...args)=>new Element(...args),formatAmount:v=>v,
 Option:function(t,v){return Object.assign(new Element('option',t),{value:v});},
 document:{documentElement:{}},getComputedStyle:()=>({fontFamily:'sans-serif',getPropertyValue:()=> '#27675b'}),
 ResizeObserver:class{observe(){}disconnect(){this.disconnected=true;}constructor(){observers.push(this);}},
 window:{addEventListener:(name,fn)=>{events[name]=fn;},removeEventListener(){}},
 echarts:{init:()=>{const chart=echarts.init(null,null,{renderer:'svg',ssr:true,width,height:400});const resize=chart.resize.bind(chart),chartWidth=width;chart.resize=()=>resize({width:chartWidth,height:400});charts.push(chart);return chart;}}
});
vm.runInContext(fs.readFileSync('web/kol.js','utf8'),context);
const data={generated_at:'2026-10-31T00:00:00Z',filters:{start:'2026-10-01',end:'2026-10-03'},groups:[{id:'k',name:'很長的合成合作夥伴名稱用來檢查圖表標籤',order_count:2,total:null,average:null}],orders:[{status:'active',ordered_at:'2026-10-01T00:00:00Z',total:'100.00',kol_id:'k',batch_id:'test'},{status:'active',ordered_at:'2026-10-03T00:00:00Z',total:null,kol_id:'k',batch_id:'test'}]};
for(width of [320,390,768,1440]){
 const root=new Element('section');context.renderKolCharts(root,data);
 assert.match(text(root),/缺訂單總金額 1 單/);
 const controls=all(root).filter(n=>n.tag==='select');controls[1].value='total';controls[1].onchange();
 assert.ok(charts.slice(-6,-3).every(c=>c.isDisposed()),'Changing metrics releases previous charts');
 for(const chart of charts.slice(-3)){const svg=chart.renderToSVGString();assert.match(svg,/<svg/);assert.doesNotMatch(svg,/NaN|Infinity/);}
 controls[0].value='month';controls[0].onchange();
 assert.deepEqual(Array.from(charts.at(-3).getOption().series[0].data),[null]);
 events.hashchange();assert.ok(charts.slice(-3).every(c=>c.isDisposed()));
}
context.echarts=null;const root=new Element('section');context.renderKolCharts(root,data);
assert.match(text(root),/圖表套件未載入/);assert.ok(all(root).filter(n=>n.tag==='details').every(n=>n.open));
console.log('KOL real ECharts SVG at 320/390/768/1440; metric/grain switching, disposal and fallback passed (not browser).');
