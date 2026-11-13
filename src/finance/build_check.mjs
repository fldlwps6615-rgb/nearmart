// 원천값 -> 셀 수식 -> 엔진 결과와 비교. 결과를 역산해 수식에 넣지 않는다.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const target = path.join(root, 'outputs/finance_validation');
const data = JSON.parse(await fs.readFile(path.join(target, 'workbook_inputs.json'), 'utf8'));
const wb = Workbook.create();
const sh = wb.worksheets.add('P1 검산');
const input = wb.worksheets.add('입력');
const refs = {};
const rows = [];
function source(key, label, value, origin, percent = false) {
  const r = rows.length + 6;
  rows.push([label, value, null, origin]);
  refs[key] = `'입력'!$C$${r}`;
  if (percent) input.getRange(`C${r}`).setNumberFormat('0.0%');
}
const p = data.parameters, f = data.finance_config;
const parameterNames = {
  gross_margin_rate: '매출총이익률', price_inflation: '가격 상승률', delivery_fee_krw: '배달비 단가',
  payment_fee_rate: '결제수수료율', packaging_krw: '기본 포장재', packaging_cold_krw: '보냉재 단가',
  shrink_rate: '폐기·감모율', marketing_rate: '상시 마케팅률', it_cost_per_order_krw: 'IT 단가',
  late_coupon_krw: '지연 쿠폰 단가', external_quick_krw: '외부 퀵 단가', contract_fee_growth: '위탁 단가 상승률',
  wage_growth: '인건비 상승률', utilities_growth: '공공요금 상승률', replenish_cost_growth: '보충 운송비 상승률',
  replenish_no_dock_surcharge: '접안 불가 할증', restoration_krw_per_m2: '㎡당 원상복구비',
  inventory_days_of_cogs: '재고 일수', tax_rate: '법인세율', hq_fixed_krw_per_year_2027: '2027년 본사비', hq_fixed_growth: '본사비 상승률',
};
for (const [key, label] of Object.entries(parameterNames)) {
  source(key, label, p[key], '15_cost_parameters.csv', /rate|growth|inflation|surcharge/.test(key));
}
for (const [key, label] of Object.entries({days_per_year:'연간 영업일', months_per_year:'연간 개월 수', krw_per_eok:'억 원 환산',
    it_growth:'IT 상승률', coupon_growth:'쿠폰 상승률', quick_growth:'퀵 상승률', maintenance_growth:'관리비 상승률', discount_rate:'할인율'})) {
  source(key, label, typeof f[key] === 'string' ? p[f[key]] : f[key], `config.json / finance.${key}`, /rate|growth/.test(key));
}
for (const [key,label] of Object.entries({orders_per_day:'하루 주문 수',avg_basket_krw:'2027년 평균 장바구니',under_40000_share:'4만 원 미만 비중',
  cold_order_share:'냉장·냉동 포함 비중',contract_rider_cost_per_order_krw:'위탁 비용 / 전체 주문',employed_rider_cost_per_order_krw:'직고용 비용 / 전체 주문',
  picker_cost_per_order_krw:'피커 비용 / 전체 주문',late_rate:'지연 주문 비중',external_quick_share:'외부 퀵 비중'})) {
  source(key,label,data.ops[key],'config 임시 운영값 → ops (2027년 단가)',/share|rate/.test(key));
}
for (const [key,label] of Object.entries({monthly_rent_krw:'P1 월세',maintenance_krw_per_month:'P1 월 관리비',truck_dock:'P1 접안 가능 여부'})) {
  source(key,label,data.pilot1[key],'03_candidate_sites.csv / P1');
}
source('p2_deposit','P2 기납부 보증금',data.pilot2.deposit_krw,'03_candidate_sites.csv / P2');
source('p2_area','P2 면적 (㎡)',data.pilot2.area_m2,'03_candidate_sites.csv / P2');
for (const [key,label] of Object.entries({fixed_staff_cost_krw_per_month:'M형 고정 인력 월비용',utilities_krw_per_month:'M형 월 공공요금',replenish_cost_krw_per_day:'M형 일 보충 운송비'})) {
  source(key,label,data.type[key],'04_hub_types.csv / M');
}
const lastInput = rows.length + 5;
input.getRange(`B6:E${lastInput}`).values = rows;
input.getRange('B2').values = [['검산 입력']];
input.getRange('B3').values = [['금액: 원 · 비율: 0~1 · 파란 숫자는 입력값']];
input.getRange('B5:E5').values = [['입력 항목','값',null,'출처']];
const labels = {
  6:'연간 주문 수',7:'상품매출',8:'배달비 매출',9:'매출 합계',10:'매출원가',11:'결제수수료',12:'기본 포장재',13:'보냉재',14:'폐기·감모',15:'상시 마케팅',
  16:'IT·고객센터',17:'지연 쿠폰',18:'외부 퀵',19:'위탁 라이더',20:'직고용 라이더',21:'피커 인건비',22:'월세',23:'관리비',24:'고정 인력',25:'공공요금',26:'보충 운송비',
  27:'개점 마케팅',28:'원상복구',29:'본사비',30:'EBITDA',31:'감가상각',32:'세전이익',33:'기초 이월결손금',34:'당기 결손금 공제',35:'과세표준',36:'기말 이월결손금',37:'법인세',38:'순이익',
  39:'최초 설비투자',40:'재투자',41:'설비투자 합계',42:'보증금 지급',43:'보증금 반환',44:'필요 재고 잔액',45:'재고 증감 투자',46:'종료 재고 회수',47:'영업현금흐름',48:'순현금흐름',49:'누적 순현금흐름',
  50:'할인 현금흐름',51:'누적 할인 현금흐름',52:'매출 (억 원)',53:'EBITDA (억 원)',54:'순현금흐름 (억 원)',
};
const engineFields = {
  6:'orders',7:'product_revenue',8:'delivery_revenue',9:'revenue',10:'cogs',11:'payment_fee',12:'packaging',13:'cold_packaging',14:'shrink',15:'ongoing_marketing',16:'it_cost',17:'late_coupon',18:'external_quick',
  19:'contract_rider_cost',20:'employed_rider_cost',21:'picker_cost',22:'rent',23:'maintenance',24:'fixed_staff',25:'utilities',26:'replenish',27:'launch_marketing',28:'restoration',29:'hq_cost',30:'ebitda',31:'depreciation',32:'ebit',
  34:'tax_loss_used',35:'taxable_profit',36:'tax_loss_carryforward',37:'tax',38:'net_income',39:'capex_initial',40:'capex_refresh',41:'capex',42:'deposit_paid',43:'deposit_returned',44:'inventory_balance',45:'inventory_investment',
  46:'inventory_recovery',47:'operating_cf',48:'cash_flow',49:'cum_cash_flow',50:'discounted_cf',51:'cum_discounted_cf',52:'revenue_eok',53:'ebitda_eok',54:'cash_flow_eok',
};
sh.getRange('B2').values = [['P1 재무 검산']];
sh.getRange('B3').values = [['P1 하루 900건 · 본사비 포함 · P2는 2027년 초 철수 · 원, 하단 억 원 병기']];
sh.getRange('B5:J5').values = [['항목',2026,2027,2028,null,'엔진 2027','차이 2027','엔진 2028','차이 2028']];
sh.getRange('B6:B54').values = Object.values(labels).map(x => [x]);
function form(col, row) {
  const r = name => refs[name], c = n => `${col}${n}`;
  const first = col === 'C', prev = String.fromCharCode(col.charCodeAt(0)-1), elapsed = `(${col}$5-$D$5)`;
  const grow = name => `(1+${r(name)})^${elapsed}`;
  const inflation = grow('price_inflation');
  if (first && ![28,30,32,33,34,35,36,37,38,41,43,44,47,48,49,50,51,52,53,54].includes(row)) return '=0';
  const formulas = {
    6:`${r('orders_per_day')}*${r('days_per_year')}`,7:`${c(6)}*${r('avg_basket_krw')}*${inflation}`,
    8:`${c(6)}*${r('under_40000_share')}*${r('delivery_fee_krw')}*${inflation}`,9:`SUM(${c(7)}:${c(8)})`,
    10:`${c(7)}*(1-${r('gross_margin_rate')})`,11:`${c(9)}*${r('payment_fee_rate')}`,12:`${c(6)}*${r('packaging_krw')}*${inflation}`,
    13:`${c(6)}*${r('cold_order_share')}*${r('packaging_cold_krw')}*${inflation}`,14:`${c(7)}*${r('shrink_rate')}`,15:`${c(7)}*${r('marketing_rate')}`,
    16:`${c(6)}*${r('it_cost_per_order_krw')}*${grow('it_growth')}`,17:`${c(6)}*${r('late_rate')}*${r('late_coupon_krw')}*${grow('coupon_growth')}`,
    18:`${c(6)}*${r('external_quick_share')}*${r('external_quick_krw')}*${grow('quick_growth')}`,
    19:`${c(6)}*${r('contract_rider_cost_per_order_krw')}*${grow('contract_fee_growth')}`,
    20:`${c(6)}*${r('employed_rider_cost_per_order_krw')}*${grow('wage_growth')}`,21:`${c(6)}*${r('picker_cost_per_order_krw')}*${grow('wage_growth')}`,
    22:`${r('monthly_rent_krw')}*${r('months_per_year')}`,23:`${r('maintenance_krw_per_month')}*${r('months_per_year')}*${grow('maintenance_growth')}`,
    24:`${r('fixed_staff_cost_krw_per_month')}*${r('months_per_year')}*${grow('wage_growth')}`,
    25:`${r('utilities_krw_per_month')}*${r('months_per_year')}*${grow('utilities_growth')}`,
    26:`${r('replenish_cost_krw_per_day')}*${r('days_per_year')}*(1+IF(${r('truck_dock')}="N",${r('replenish_no_dock_surcharge')},0))*${grow('replenish_cost_growth')}`,
    27:'0',28:first ? `${r('p2_area')}*${r('restoration_krw_per_m2')}` : '0',29:`${r('hq_fixed_krw_per_year_2027')}*${grow('hq_fixed_growth')}`,
    30:`${c(9)}-SUM(${c(10)}:${c(29)})`,31:'0',32:`${c(30)}-${c(31)}`,33:first?'0':`${prev}36`,
    34:`MIN(${c(33)},MAX(0,${c(32)}))`,35:`MAX(0,${c(32)}-${c(34)})`,36:`${c(33)}+MAX(0,-${c(32)})-${c(34)}`,
    37:`${c(35)}*${r('tax_rate')}`,38:`${c(32)}-${c(37)}`,39:'0',40:'0',41:`SUM(${c(39)}:${c(40)})`,42:'0',43:first?r('p2_deposit'):'0',
    44:first?`${r('orders_per_day')}*${r('avg_basket_krw')}*(1-${r('gross_margin_rate')})*${r('inventory_days_of_cogs')}`:`${c(10)}/${r('days_per_year')}*${r('inventory_days_of_cogs')}`,
    45:first?'0':`${c(44)}-${prev}44`,46:'0',47:`${c(30)}-${c(37)}-${c(45)}+${c(46)}`,48:`${c(47)}-${c(41)}-${c(42)}+${c(43)}`,
    49:first?c(48):`${prev}49+${c(48)}`,50:`${c(48)}/(1+${r('discount_rate')})^(${col}$5-$C$5)`,51:first?c(50):`${prev}51+${c(50)}`,
    52:`${c(9)}/${r('krw_per_eok')}`,53:`${c(30)}/${r('krw_per_eok')}`,54:`${c(48)}/${r('krw_per_eok')}`,
  };
  return '=' + formulas[row];
}
sh.getRange('C6:E54').formulas = Array.from({length:49}, (_,i)=>['C','D','E'].map(c=>form(c,i+6)));
for (const [row,field] of Object.entries(engineFields)) {
  sh.getRange(`G${row}`).values = [[data.annual[1][field]]];
  sh.getRange(`I${row}`).values = [[data.annual[2][field]]];
  sh.getRange(`H${row}`).formulas = [[`=D${row}-G${row}`]];
  sh.getRange(`J${row}`).formulas = [[`=E${row}-I${row}`]];
}
for (const sheet of [sh,input]) {
  const extent = sheet===sh?'B2:J58':`B2:E${lastInput+2}`;
  sheet.showGridLines=false;
  sheet.getRange(extent).format.font={name:'Malgun Gothic',size:10,color:'#17263A'};
  sheet.getRange(extent).format.rowHeight=22;
  sheet.getRange(extent).format.verticalAlignment='center';
  sheet.getRange('B2').format.font={name:'Malgun Gothic',size:15,bold:true,color:'#17263A'};
  sheet.getRange('B3').format.font={name:'Malgun Gothic',size:9,italic:true,color:'#5F6875'};
  sheet.getRange(sheet===sh?'B5:J5':'B5:E5').format={fill:'#253D55',font:{name:'Malgun Gothic',size:10,bold:true,color:'#FFFFFF'},rowHeight:26};
  sheet.getRange('B:B').format.columnWidth=30;
  sheet.freezePanes.freezeRows(5);
}
sh.getRange('C:J').format.columnWidth=19;
sh.getRange('F:F').format.columnWidth=3;
sh.getRange('C6:J51').setNumberFormat('#,##0;(#,##0);"-"');
sh.getRange('C52:J54').setNumberFormat('0.0000;(0.0000);"-"');
sh.getRange('H6:H54').setNumberFormat('0.00;(0.00);0.00');
sh.getRange('J6:J54').setNumberFormat('0.00;(0.00);0.00');
sh.getRange('G6:G54').format.font.color='#0000FF';
sh.getRange('I6:I54').format.font.color='#0000FF';
sh.getRange('F5:F54').format.fill='#FFFFFF';
for(const row of [9,30,32,38,41,48,49,51]) {
  sh.getRange(`B${row}:E${row}`).format.fill='#E9EFF5';
  sh.getRange(`B${row}:E${row}`).format.font.bold=true;
  sh.getRange(`B${row}:E${row}`).format.borders={bottom:{style:'thin',color:'#ADBACA'}};
}
sh.getRange('B56').values=[['차이 = 셀 수식 결과 − 엔진 결과. 원 단위까지 비교하며 내부 계산은 반올림하지 않습니다.']];
sh.getRange('B57').values=[['입력값을 바꾸면 수식은 재계산됩니다. 엔진 비교값은 검증 실행 시점의 고정값입니다.']];
sh.getRange('B58').values=[['2026년 말 P1 재고는 매몰 기초재고이며 현금 지출이 아닙니다. P2 철수손실은 이월공제합니다.']];
sh.getRange('H6:H54').conditionalFormats.addCustom('ABS(H6)>=0.5',{fill:'#FBE8E5',font:{color:'#9C2020',bold:true}});
sh.getRange('J6:J54').conditionalFormats.addCustom('ABS(J6)>=0.5',{fill:'#FBE8E5',font:{color:'#9C2020',bold:true}});
input.getRange('C:C').format.columnWidth=23;
input.getRange('D:D').format.columnWidth=3;
input.getRange('E:E').format.columnWidth=61;
input.getRange(`C6:C${lastInput}`).format.font.color='#0000FF';
input.getRange(`C6:C${lastInput}`).setNumberFormat('#,##0.########;(#,##0.########);"-"');
input.getRange(`E6:E${lastInput}`).format.font.color='#64748B';
for(const [name,ref] of Object.entries(refs)) if(/rate|growth|inflation|surcharge|share/.test(name)) input.getRange(ref.split('!')[1]).setNumberFormat('0.0%');
for(let row=6;row<=54;row++) for(const col of ['C','D','E']) if(form(col,row).includes("'입력'!")) sh.getRange(`${col}${row}`).format.font.color='#008000';

wb.recalculate();
const differences=[];
for(const [row,field] of Object.entries(engineFields)) for(const [col,engineCol,year] of [['D','G',2027],['E','I',2028]]) {
  const actual=sh.getRange(`${col}${row}`).values[0][0], expected=sh.getRange(`${engineCol}${row}`).values[0][0];
  if(typeof actual!=='number'||!Number.isFinite(actual)) throw new Error(`계산 오류 ${col}${row}: ${actual}`);
  const diff=actual-expected;
  if(Math.abs(diff)>=0.5) throw new Error(`원 단위 불일치 ${field} ${year}: ${diff}`);
  differences.push({year,field,actual,expected,difference:diff});
}
// 입력 편집 후 재계산이 실제로 결과를 바꾸는지 검증하고 원상 복구한다.
const qCell=input.getRange(refs.orders_per_day.split('!')[1]), original=qCell.values[0][0];
const revenueBefore=sh.getRange('D9').values[0][0];
qCell.values=[[original*2]];
wb.recalculate();
const editResponded=Math.abs(sh.getRange('D9').values[0][0]-revenueBefore*2)<0.001;
if(!editResponded) throw new Error('주문 수 입력 재계산 실패');
qCell.values=[[original]];
wb.recalculate();
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:50},summary:'수식 오류 검색',maxChars:2000});
console.log(errors.ndjson);
console.log((await wb.inspect({kind:'table',range:"'P1 검산'!B47:J54",include:'values,formulas',tableMaxRows:8,tableMaxCols:9,maxChars:3000})).ndjson);
await fs.mkdir(path.join(root,'report'),{recursive:true});
await (await SpreadsheetFile.exportXlsx(wb)).save(path.join(root,'report/finance_check.xlsx'));
for(const [sheetName,range,name] of [['P1 검산','B2:J30','check_profit'],['P1 검산','B30:J58','check_cash'],['입력',`B2:E${lastInput}`,'check_inputs']]) {
  const png=await wb.render({sheetName,range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(target,`${name}.png`),new Uint8Array(await png.arrayBuffer()));
}
const check={compared_cells:differences.length,max_absolute_difference_krw:Math.max(...differences.map(x=>Math.abs(x.difference))),all_match_to_won:true,input_edit_recalculated:editResponded,engine:'Artifact Tool',native_excel_recalculation_verified:false,differences};
await fs.writeFile(path.join(target,'workbook_check.json'),JSON.stringify(check,null,2));
const summary=JSON.parse(await fs.readFile(path.join(target,'summary.json'),'utf8'));
summary.b.workbook_check={compared_cells:check.compared_cells,max_absolute_difference_krw:check.max_absolute_difference_krw,all_match_to_won:true,input_edit_recalculated:editResponded};
await fs.writeFile(path.join(target,'summary.json'),JSON.stringify(summary,null,2));
console.log(JSON.stringify({...check,differences:undefined}));
