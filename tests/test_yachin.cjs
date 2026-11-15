// No browser, network, or package dependency: execute the real inline app functions.
// The minimal DOM below supports rendering assertions only, not layout/accessibility.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const html = fs.readFileSync(path.join(__dirname, '../pages/yachin/index.html'), 'utf8');
const inline = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const hooks = `globalThis.app={freshState,decide,renderFirst,renderUrgent,renderCandidates,renderFind,renderPlan,renderAfter,memoText,callScript,landlordScript,checkList,officeOpen,nextWeekdayLabel,daysLeft,calLink,load,save,clearAll,copyText,setMemo,getState:()=>state,setState:s=>{state=Object.assign(freshState(),s)}};`;
const source = inline.slice(0, inline.lastIndexOf('  renderQuestions();')) + hooks + '\n})();';
function appAt(when='2026-11-16T01:00:00Z', saved=null) {
  const elements = new Map(); const handlers = {};
  function el(id) { if (!elements.has(id)) elements.set(id,{id,innerHTML:'',textContent:'',value:'',hidden:false,checked:false,style:{},scrollHeight:100,children:[],classList:{toggle(){},add(){},remove(){}},querySelectorAll(){return []},addEventListener(){}}); return elements.get(id); }
  class Clock extends Date { constructor(...a) { super(...(a.length?a:[when])); } static now(){return new Date(when).getTime();} }
  const mem = new Map(saved===null?[]:[['yachin-tsugi-no-ippo-v1',saved]]);
  const context = {Date:Clock,console,setTimeout:()=>0,clearTimeout(){},navigator:{},document:{getElementById:el,querySelectorAll:()=>[],addEventListener:(type,fn)=>{handlers[type]=fn}},window:{addEventListener(){}},localStorage:{getItem:k=>mem.get(k)||null,setItem:(k,v)=>mem.set(k,v),removeItem:k=>mem.delete(k)}};
  vm.createContext(context); vm.runInContext(source,context);
  return {a:context.app,el,mem,handlers};
}
function ids(a){return Array.from(a.decide().c,x=>x.id);}
test('income reduction over two years remains a consultation candidate',()=>{const {a}=appAt(); a.setState({a:{reason:'cut',when:'over2'}}); assert.ok(ids(a).includes('jukyo'));});
test('job loss over two years still explains extension exceptions',()=>{const {a}=appAt(); a.setState({a:{reason:'jobloss',when:'over2'}}); assert.match(a.decide().ex.join(''),/病気|疾病/);});
test('pension household can ask about relocation independently of rent subsidy',()=>{const {a}=appAt(); a.setState({a:{reason:'pension',work:'retired',move:'ok'}}); assert.ok(ids(a).includes('tenkyo')); assert.ok(!ids(a).includes('jukyo'));});
test('no pension/retirement exclusion from temporary loan with repayment prospect',()=>{const {a}=appAt(); a.setState({a:{reason:'pension',work:'retired',outlook:'back'}}); assert.ok(ids(a).includes('koguchi'));});
test('life emergency overrides eviction and food in first action',()=>{const {a,el}=appAt(); a.setState({a:{rent:'evict'},safety:{life:true,food:true}});a.renderFirst(); assert.match(el('firstOut').innerHTML,/安全|命/); assert.doesNotMatch(el('firstOut').innerHTML,/法テラスに電話/);});
test('life-only first action does not give ordinary rent script',()=>{const {a,el}=appAt();a.setState({safety:{life:true}});a.renderFirst();assert.doesNotMatch(el('firstOut').innerHTML,/say_first_call/);});
test('emergency contacts precede welfare office in mixed urgent panel',()=>{const {a,el}=appAt();a.setState({safety:{life:true,night:true}});a.renderUrgent();const s=el('urgentOut').innerHTML;assert.ok(s.indexOf('tel:119')<s.indexOf('福祉事務所'));});
test('welfare recipient first action is existing caseworker',()=>{const {a,el}=appAt();a.setState({a:{welfare:'yes'}});a.renderFirst();assert.match(el('firstOut').innerHTML,/ケースワーカー/);});
test('holiday is closed and next day skips it',()=>{const {a}=appAt('2026-11-23T01:00:00Z');assert.equal(a.officeOpen(),false);assert.match(a.nextWeekdayLabel(),/11月24日/);});
test('substitute holiday and consecutive holidays skipped',()=>{const {a}=appAt('2026-05-01T10:00:00Z');assert.match(a.nextWeekdayLabel(),/5月7日/);});
test('year end skips through next year opening day',()=>{const {a}=appAt('2026-12-28T10:00:00Z');assert.match(a.nextWeekdayLabel(),/1月4日/);});
test('unknown holiday year does not assert precise next opening date',()=>{const {a}=appAt('2028-01-04T01:00:00Z');assert.equal(a.officeOpen(),false);assert.match(a.nextWeekdayLabel(),/開く|確認/);});
test('Japanese opening hours independent of host timezone',()=>{const {a}=appAt('2026-11-16T00:00:00Z');assert.equal(a.officeOpen(),true);});
test('memo includes urgent living needs and existing welfare status',()=>{const {a}=appAt();a.setState({a:{welfare:'yes'},safety:{night:true,food:true}});const s=a.memoText(a.decide());assert.match(s,/今夜.*寝る/);assert.match(s,/食べ物/);assert.match(s,/生活保護を受け/);});
test('unchecked consultation is not claimed already done to landlord',()=>{const {a}=appAt();a.setState({a:{rent:'now'}});assert.doesNotMatch(a.landlordScript(),/相談しています/);});
test('checklist completion stays attached to content after reordering',()=>{const {a}=appAt();let first=a.checkList('bring',['通帳','契約書']);let key=first.match(/data-chk="([^"]+)"/)[1];a.getState().checks[key]=true;const second=a.checkList('bring',['新しい持ち物','通帳','契約書']);assert.match(second.split('</li>')[1],/ checked/);assert.doesNotMatch(second.split('</li>')[0],/ checked/);});
test('tampered saved nested fields cannot crash app on load',()=>{const {a}=appAt(undefined,JSON.stringify({a:{},safety:null,log:'broken',name:42,after:'unknown'}));a.load();assert.doesNotThrow(()=>{a.decide();a.renderFirst();a.renderAfter();a.memoText(a.decide());});});
test('input is escaped in scripts and search, and calendar disclosure is adjacent',()=>{const {a,el}=appAt();a.setState({city:'<img src=x>',name:'<script>x</script>'});a.renderFirst();a.renderFind();assert.doesNotMatch(el('firstOut').innerHTML,/<script>/);assert.match(el('findOut').innerHTML,/%3Cimg/);assert.match(a.calLink('2026-11-20','相談'),/Google.*送|送.*Google/);});
test('saving is opt in and clear removes the saved app record',()=>{const {a,mem}=appAt();assert.equal(mem.size,0);a.save();assert.equal(mem.size,1);a.clearAll();assert.equal(mem.size,0);});
test('manual memo survives changing answers',()=>{const {a,el}=appAt();a.setState({memoCustom:'自分の言葉',memoBase:'以前',a:{rent:'now'}});a.setMemo();assert.equal(el('memoOut').value,'自分の言葉');assert.equal(el('memoStale').hidden,false);});
test('DV and housing emergency both remain available with safety first',()=>{const {a,el}=appAt();a.setState({safety:{dv:true,night:true}});a.renderFirst();a.renderUrgent();assert.match(el('firstOut').innerHTML,/DV/);assert.match(el('urgentOut').innerHTML,/福祉事務所/);});
test('safety first across all 16 emergency combinations and 6 rent states',()=>{const {a,el}=appAt();for(let bits=0;bits<16;bits++)for(const rent of ['', 'soon','now','late1','late2','evict']){const safety=Object.fromEntries(['night','food','life','dv'].map((k,i)=>[k,!!(bits&(1<<i))]));a.setState({a:{rent},safety});a.renderFirst();a.renderPlan(a.decide());if(safety.life){assert.match(el('firstOut').innerHTML,/まず命と体/);assert.match(el('planOut').innerHTML.split('</li>')[0],/命と体/);}else if(safety.dv)assert.match(el('firstOut').innerHTML,/DV/);else if(safety.night||safety.food)assert.match(el('firstOut').innerHTML,/福祉事務所/);}});
test('all five consultation outcomes render and escape malicious text',()=>{const {a,el}=appAt();for(const after of ['applied','next','other','refused','notyet']){a.setState({after,name:'<img onerror=bad>',a:{nat:'foreign'}});a.renderAfter();assert.ok(el('afterOut').innerHTML.length>100);assert.doesNotMatch(el('afterOut').innerHTML,/<img/);}});
test('calendar end date crosses year without sending name or notes',()=>{const {a}=appAt();a.setState({name:'private-name',afterNote:'private-note'});const s=a.calLink('2026-12-31','相談の予約・期限');assert.match(s,/20261231\/20270101/);assert.doesNotMatch(s,/private/);});
test('deadline count follows Japanese date near midnight',()=>{const {a}=appAt('2026-11-15T16:00:00Z');a.setState({payday:'2026-11-17'});assert.equal(a.daysLeft(),1);});
test('failed storage erasure is not reported as success',()=>{const {a,el,mem}=appAt();a.save();mem.delete=()=>{throw Error('blocked')};a.clearAll();assert.match(el('status').textContent,/削除できません/);});
test('copy failure gives phone number fallback',()=>{const {a,el}=appAt();a.copyText('0570-078374',{textContent:'番号をコピー'});assert.match(el('status').textContent,/コピーできません/);});
