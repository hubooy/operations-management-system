"""Trusted native Excel formulas for a separate, unreviewed budget model.

The source report is unchanged. Allocation uses quotient/remainder arithmetic;
scenario ratios cancel integer factors before rounding. Products outside the
verified integer range stay unavailable instead of silently losing cents.
"""
from dataclasses import dataclass, field
from fractions import Fraction
from math import gcd
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

from .budget import calculate, rounded
from .contracts import AnalysisContractError
from .report_files import NS, cell, column_name

TITLES = ("预算试算_可编辑", "预算分配计算", "预算情景计算")
INTEGER_LIMIT = 99999999999999


@dataclass
class Sheet:
    name: str
    cells: dict = field(default_factory=dict)
    inputs: list = field(default_factory=list)
    widths: dict = field(default_factory=dict)
    headers: set = field(default_factory=lambda: {4})
    merges: list = field(default_factory=list)
    row_heights: dict = field(default_factory=dict)

    def header(self, row, col, value):
        self.put(row, col, value)
        self.cells[row, col] = (value, None, 1)
        self.headers.add(row)

    def put(self, row, col, value, formula=None, *, editable=False, lo=0, hi=10**10, whole=False, nullable=False):
        address = column_name(col)+str(row)
        self.cells[row, col] = (value, formula, 6 if editable else 1 if row == 4 else 5 if row == 3 else 2 if type(value) is int else 3 if type(value) is float else 0)
        if editable:
            self.inputs.append((address, lo, hi, whole, nullable))
        return address


def styles(value):
    root = ET.fromstring(value)
    fonts, fills, formats = (root.find('{'+NS+'}'+name) for name in ("fonts", "fills", "cellXfs"))
    font_index, fill_index = len(fonts), len(fills)
    fonts.append(ET.fromstring(f'<font xmlns="{NS}"><sz val="11"/><color rgb="FF1757B8"/><name val="Microsoft YaHei"/></font>'))
    fills.append(ET.fromstring(f'<fill xmlns="{NS}"><patternFill patternType="solid"><fgColor rgb="FFFFF4CF"/><bgColor indexed="64"/></patternFill></fill>'))
    formats.append(ET.fromstring(f'<xf xmlns="{NS}" numFmtId="0" fontId="{font_index}" fillId="{fill_index}" borderId="0" xfId="0" applyProtection="1"><protection locked="0"/></xf>'))
    for element in (fonts, fills, formats): element.set("count", str(len(element)))
    return ET.tostring(root, encoding="unicode")


def write_sheet(archive, number, sheet):
    max_row = max(r for r, _ in sheet.cells)
    max_col = max(c for _, c in sheet.cells)
    with archive.open(f"xl/worksheets/sheet{number}.xml", "w", force_zip64=True) as target:
        def out(text): target.write(text.encode())
        out(f'<worksheet xmlns="{NS}"><dimension ref="A1:{column_name(max_col)}{max_row}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" xSplit="1" topLeftCell="B5" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><cols>')
        for c in range(1, max_col+1):
            out(f'<col min="{c}" max="{c}" width="{sheet.widths.get(c, 22)}" customWidth="1"/>')
        out('</cols><sheetData>')
        for r in range(1, max_row+1):
            out(f'<row r="{r}" ht="{sheet.row_heights.get(r, 44 if r in sheet.headers else 28)}" customHeight="1">')
            for c in range(1, max_col+1):
                if (r, c) not in sheet.cells: continue
                value, formula, style = sheet.cells[r, c]
                address = column_name(c)+str(r)
                if formula is None:
                    out(cell(value, address, style))
                else:
                    if len(formula) > 8192: raise AnalysisContractError("预算公式超过 Excel 容量")
                    kind = "str" if value is None or isinstance(value, str) else "n"
                    cached = "" if value is None else escape(str(value))
                    out(f'<c r="{address}" s="{style}" t="{kind}"><f>{escape(formula)}</f><v>{cached}</v></c>')
            out('</row>')
        out('</sheetData><sheetProtection sheet="1" objects="1" scenarios="1"/>')
        if sheet.merges:
            out(f'<mergeCells count="{len(sheet.merges)}">'+''.join(f'<mergeCell ref="{reference}"/>' for reference in sheet.merges)+'</mergeCells>')
        if sheet.inputs:
            out(f'<dataValidations count="{len(sheet.inputs)}">')
            for address, lo, hi, whole, nullable in sheet.inputs:
                out(f'<dataValidation type="{"whole" if whole else "decimal"}" operator="between" allowBlank="{int(nullable)}" showErrorMessage="1" errorTitle="输入范围无效" error="请使用允许范围内的参数" sqref="{address}"><formula1>{lo}</formula1><formula2>{hi}</formula2></dataValidation>')
            out('</dataValidations>')
        out('<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>')


def build(value, names, *, formula_version=1):
    if type(formula_version) is not int or formula_version not in (1, 2):
        raise AnalysisContractError("预算公式版本不受支持")
    def rem(numerator, denominator):
        # Excel MOD can return #NUM for large integer quotients. All callers
        # are nonnegative integers bounded by 1e14 (allocation <=1e12).
        # Quotient*divisor and subtraction therefore remain exact integers
        # below 2**53. Preserve the historical formula bytes by default.
        if formula_version == 1:
            return f'MOD({numerator},{denominator})'
        return f'({numerator}-QUOTIENT({numerator},{denominator})*{denominator})'

    plan, bases = value["plan"], value["baselines"]
    expected = calculate(plan, bases)
    n, s = len(bases), len(plan["scenarios"])
    if len(names) != 3 or len(set(names)) != 3:
        raise AnalysisContractError("预算工作表身份无效")
    sheets = [Sheet(name) for name in names]
    main, allocation, forecast = sheets
    def ref(which, col, row): return "'"+names[which].replace("'", "''")+"'!"+column_name(col)+str(row)
    def rng(col): return f'${column_name(col)}$5:${column_name(col)}${n+4}'
    mr = lambda row: ref(0, 2, row)
    ar = lambda col, row: ref(1, col, row)
    fr = lambda col, row: ref(2, col, row)
    alloc_range = lambda col: ar(col, 5)+":"+ar(col, n+4)
    forecast_range = lambda col: fr(col, 5)+":"+fr(col, n+4)
    cases = lambda col: ref(0, col, 16)+":"+ref(0, col, s+15)
    selected = lambda col: f'INDEX({cases(col)},{mr(12)})'
    for sheet, title in zip(sheets, TITLES):
        sheet.put(3, 1, title)
        sheet.put(1, 1, "未复核试算；原报告数据不变")
    for sheet in (allocation, forecast):
        sheet.put(3, 4, "当前情景")
        sheet.put(3, 5, plan["scenarios"][0]["name"], ref(0, 5, 11))
    main.widths = {1: 30, 2: 22, 3: 24, 4: 30, 5: 27, 6: 24, 7: 24, 8: 30}
    main.merges = ["A1:H1", "A2:H2", "A3:H3", "A23:H23", "A24:H24"]
    main.put(2, 1, "蓝字浅黄色格可修改；每次选择一个情景计算。超精度结果请使用 HTML 试算。")
    globals_ = [(5,"预算上限（元）","totalBudgetCents",100, .01,10**10), (6,"预留（元）","reserveCents",100,0,10**10),
        (7,"规划天数","horizonDays",1,1,93),(8,"观察天数","observationDays",1,1,93),
        (9,"提前复盘花费占比（%）","reviewAfterSpendBps",100,.01,100),(10,"样本点击门槛","minimumClicks",1,1,1000000),(11,"样本订单门槛","minimumOrderLines",1,1,1000000)]
    for r, label, key, scale, lo, hi in globals_:
        main.put(r, 1, label); main.put(r, 2, plan[key]/scale, editable=True, lo=lo, hi=hi, whole=scale==1)
    main.put(12, 1, "当前情景序号"); main.put(12, 2, 1, editable=True, lo=1, hi=s, whole=True)
    for c, label in enumerate(("序号","情景名称","点击成本相对基期（%）","订单效率相对基期（%）","订单金额相对基期（%）","假设贡献率（%，可空）","参数有效（1/0）"), 1): main.header(15,c,label)
    for i, scenario in enumerate(plan["scenarios"],16):
        main.put(i,1,i-15); main.put(i,2,scenario["name"])
        for c,key in enumerate(("cpcFactorBps","orderRateFactorBps","orderValueFactorBps","contributionMarginBps"),3):
            main.put(i,c,None if scenario[key] is None else scenario[key]/100,editable=True,lo=0 if c==6 else 10,hi=100 if c==6 else 300,nullable=c==6)
        valid = f'AND(MIN(C{i}:E{i})>=10,MAX(C{i}:E{i})<=300,C{i}=ROUND(C{i},2),D{i}=ROUND(D{i},2),E{i}=ROUND(E{i},2),IF(ISBLANK(F{i}),TRUE,IF(ISNUMBER(F{i}),AND(F{i}>=0,F{i}<=100,F{i}=ROUND(F{i},2)),FALSE)))'
        main.put(i,7,1,f'IF(COUNT(C{i}:E{i})=3,IF({valid},1,0),0)')
    headers = ("预算对象","权重","最低预算（元）","最高预算（元）","最低产出比（倍）","基期推广费（分）","基期点击","基期订单口径","基期归因金额（分）","基期天数","日期完整（1/0）","平台归因口径","对象输入有效","最低预算（分）","可追加上限（分）","上限权重整商","上限权重余数","触顶顺序","固定尾差顺序","此前上限合计","剩余对象权重","该点可用追加","已触顶（1/0）","未触顶整分","未触顶余数","余数顺序","最终预算（分）","基数可测算","低样本（1/0）","责任角色","分析行ID")
    for c,label in enumerate(headers,1): allocation.put(4,c,label)
    allocation.widths[1]=35
    targets=plan["targets"]; minima=[t["minBudgetCents"] for t in targets]; caps=[t["maxBudgetCents"]-t["minBudgetCents"] for t in targets]; weights=[t["weight"] for t in targets]
    identity=sorted(range(n),key=lambda i:(targets[i]["sourceKey"],targets[i]["dimension"],targets[i]["rowId"]))
    order=sorted(range(n),key=lambda i:(Fraction(caps[i],weights[i]),identity.index(i)))
    ranks=[order.index(i)+1 for i in range(n)]
    available=plan["totalBudgetCents"]-plan["reserveCents"]; funding=min(available-sum(minima),sum(caps))
    prefix=[sum(caps[j] for j in range(n) if ranks[j]<ranks[i]) for i in range(n)]
    suffix=[sum(weights[j] for j in range(n) if ranks[j]>=ranks[i]) for i in range(n)]
    candidate=[max(0,funding-prefix[i]) for i in range(n)]
    capped=[int(candidate[i]*weights[i]//suffix[i]>=caps[i]) for i in range(n)]
    residual=funding-sum(caps[i] for i in range(n) if capped[i]); active=sum(weights[i] for i in range(n) if not capped[i])
    floors=[0 if capped[i] else residual*weights[i]//active for i in range(n)]
    remainders=[-1 if capped[i] else residual*weights[i]%active for i in range(n)]
    remaining=residual-sum(floors)
    rr=sorted((i for i in range(n) if not capped[i]),key=lambda i:(-remainders[i],identity.index(i)))
    amounts=[minima[i]+(caps[i] if capped[i] else floors[i]+int(rr.index(i)<remaining)) for i in range(n)]
    if amounts != [r["budgetCents"] for r in expected["scenarios"][0]["rows"]]: raise AnalysisContractError("Excel 分配初值不一致")
    global_valid=f'AND(COUNT({mr(5)}:{mr(12)})=8,{mr(5)}>0,{mr(5)}<=10000000000,{mr(5)}=ROUND({mr(5)},2),{mr(6)}>=0,{mr(6)}<={mr(5)},{mr(6)}=ROUND({mr(6)},2),{mr(7)}>=1,{mr(7)}<=93,{mr(7)}=INT({mr(7)}),{mr(8)}>=1,{mr(8)}<={mr(7)},{mr(8)}=INT({mr(8)}),{mr(9)}>0,{mr(9)}<=100,{mr(9)}=ROUND({mr(9)},2),{mr(10)}>=1,{mr(10)}<=1000000,{mr(10)}=INT({mr(10)}),{mr(11)}>=1,{mr(11)}<=1000000,{mr(11)}=INT({mr(11)}),{mr(12)}>=1,{mr(12)}<={s},{mr(12)}=INT({mr(12)}),SUM({rng(13)})={n},SUM({cases(7)})={s})'
    allocation.put(1,8,1,f'IF(COUNT({mr(5)}:{mr(12)})=8,IF({global_valid},IF(SUM({rng(14)})<=ROUND({mr(5)}*100,0)-ROUND({mr(6)}*100,0),1,0),0),0)')
    allocation.put(1,2,available,f'IF($H$1,ROUND({mr(5)}*100,0)-ROUND({mr(6)}*100,0),0)')
    allocation.put(1,4,sum(minima),f'SUM({rng(14)})'); allocation.put(1,6,funding,f'IF($H$1,MIN(B1-D1,SUM({rng(15)})),0)')
    allocation.put(2,2,residual,f'IF($H$1,F1-SUMIF({rng(23)},1,{rng(15)}),0)')
    allocation.put(2,4,active,f'SUMIF({rng(23)},0,{rng(2)})'); allocation.put(2,6,remaining,f'IF($H$1,B2-SUM({rng(24)}),0)')
    same_basis=len({b.get("source","unspecified") for b in bases})==1
    allocation.put(2,8,int(same_basis),f'IF(COUNTIF({rng(12)},L5)={n},1,0)')
    for r,c,label in ((1,1,"可用预算（分）"),(1,3,"最低预算合计"),(1,5,"本轮追加额"),(1,7,"输入有效（1/0）"),(2,1,"未触顶待分额"),(2,3,"未触顶权重"),(2,5,"待分尾差（分）"),(2,7,"单一成交口径")): allocation.put(r,c,label)
    for i,(t,b) in enumerate(zip(targets,bases)):
        r=i+5; facts=b["metrics"]; obj=" · ".join(str(v) for v in b["entity"].values() if v is not None)
        allocation.put(r,1,obj)
        for c,v,hi,whole in ((2,t["weight"],10000,True),(3,t["minBudgetCents"]/100,10**10,False),(4,t["maxBudgetCents"]/100,10**10,False),(5,t["minimumRoasBps"]/10000,100,False)):
            allocation.put(r,c,v,editable=True,lo=1 if c==2 else 0,hi=hi,whole=whole)
        for c,key in enumerate(("spendCents","clicks","reportedOrderLines","reportedGmvCents"),6): allocation.put(r,c,facts.get(key))
        for c,v in ((10,b["days"]),(11,int(b["datesPresent"])),(12,b.get("source","unspecified")),(19,identity.index(i)+1),(30,t["ownerRole"]),(31,t["rowId"])): allocation.put(r,c,v)
        f=f'AND(COUNT(B{r}:E{r})=4,B{r}>=1,B{r}<=10000,B{r}=INT(B{r}),C{r}>=0,C{r}<=D{r},D{r}<=10000000000,C{r}=ROUND(C{r},2),D{r}=ROUND(D{r},2),E{r}>=0,E{r}<=100,E{r}=ROUND(E{r},4))'
        allocation.put(r,13,1,f'IF(COUNT(B{r}:E{r})=4,IF({f},1,0),0)')
        allocation.put(r,14,minima[i],f'IF(M{r},ROUND(C{r}*100,0),0)'); allocation.put(r,15,caps[i],f'IF(M{r},ROUND(D{r}*100,0)-N{r},0)')
        allocation.put(r,16,caps[i]//weights[i],f'IF($H$1,QUOTIENT(O{r},B{r}),0)'); allocation.put(r,17,caps[i]%weights[i],f'IF($H$1,{rem(f"O{r}", f"B{r}")},0)')
        allocation.put(r,18,ranks[i],f'IF($H$1,1+COUNTIF({rng(16)},"<"&P{r})+SUMPRODUCT(({rng(16)}=P{r})*({rng(17)}*B{r}<Q{r}*{rng(2)}))+SUMPRODUCT(({rng(16)}=P{r})*({rng(17)}*B{r}=Q{r}*{rng(2)})*({rng(19)}<S{r})),0)')
        allocation.put(r,20,prefix[i],f'SUMIF({rng(18)},"<"&R{r},{rng(15)})'); allocation.put(r,21,suffix[i],f'SUMIF({rng(18)},">="&R{r},{rng(2)})')
        allocation.put(r,22,candidate[i],f'MAX(0,$F$1-T{r})')
        allocation.put(r,23,capped[i],f'IF($H$1,IF(QUOTIENT(V{r},U{r})*B{r}+QUOTIENT({rem(f"V{r}", f"U{r}")}*B{r},U{r})>=O{r},1,0),0)')
        allocation.put(r,24,floors[i],f'IF($H$1,IF(W{r},0,QUOTIENT($B$2,$D$2)*B{r}+QUOTIENT({rem("$B$2", "$D$2")}*B{r},$D$2)),0)')
        allocation.put(r,25,remainders[i],f'IF($H$1,IF(W{r},-1,{rem(rem("$B$2", "$D$2")+f"*B{r}", "$D$2")}),-1)')
        allocation.put(r,26,0 if capped[i] else rr.index(i)+1,f'IF(OR(NOT($H$1),W{r}),0,1+COUNTIF({rng(25)},">"&Y{r})+COUNTIFS({rng(25)},Y{r},{rng(19)},"<"&S{r}))')
        allocation.put(r,27,amounts[i],f'IF($H$1,N{r}+IF(W{r},O{r},X{r}+IF(Z{r}<=$F$2,1,0)),"")')
        usable=expected["scenarios"][0]["rows"][i]["status"]!="unavailable"
        allocation.put(r,28,int(usable),f'IF(AND(COUNT(F{r}:K{r})=6,F{r}>0,G{r}>0,H{r}>0,I{r}>=0,J{r}>=1,J{r}<=93,K{r}=1),1,0)')
        low=bool(facts.get("clicks") is not None and facts.get("reportedOrderLines") is not None and (facts["clicks"]<plan["minimumClicks"] or facts["reportedOrderLines"]<plan["minimumOrderLines"]))
        allocation.put(r,29,int(low),f'IF(AND(COUNT(G{r}:H{r})=2,OR(G{r}<{mr(10)},H{r}<{mr(11)})),1,0)')

    headings=("对象","分配预算（分）","基期花费（分）","基期归因金额（分）","点击成本乘数基点","订单效率乘数基点","订单金额乘数基点","贡献率基点（可空）","基数可测算","情景归因金额（分）","归因产出比","假设贡献扣推广（分）","提前复盘花费（分）","等天数基期花费（分）","预算差额（分）","试算状态","观察天数","责任角色","最低归因产出比","复算精度状态","基期对比状态")
    for c,label in enumerate(headings,1): forecast.put(4,c,label)
    forecast.widths.update({16: 45, 21: 32})
    outputs=[]; a=plan["scenarios"][0]
    for i,(t,b) in enumerate(zip(targets,bases)):
        r=i+5; expected_row=expected["scenarios"][0]["rows"][i]; facts=b["metrics"]; cursor=22
        forecast.row_heights[r] = 56
        usable=expected_row["status"]!="unavailable"
        for c,value_,formula in ((1,allocation.cells[r,1][0],ar(1,r)),(2,amounts[i],ar(27,r)),(3,facts.get("spendCents"),f'IF(COUNT({ar(6,r)})=1,{ar(6,r)},"")'),(4,facts.get("reportedGmvCents"),f'IF(COUNT({ar(9,r)})=1,{ar(9,r)},"")')): forecast.put(r,c,value_,formula)
        for c,key,sourcecol in ((5,"cpcFactorBps",3),(6,"orderRateFactorBps",4),(7,"orderValueFactorBps",5)):
            forecast.put(r,c,a[key],f'IF({ar(8,1)},ROUND({selected(sourcecol)}*100,0),"")')
        blank_margin = 'OR('+','.join(f'AND({mr(12)}={j+1},ISBLANK({ref(0,6,j+16)}))' for j in range(s))+')'
        forecast.put(r,8,a["contributionMarginBps"],f'IF({ar(8,1)},IF({blank_margin},"",ROUND({selected(6)}*100,0)),"")')
        forecast.put(r,9,int(usable),f'IF(AND({ar(8,1)},{ar(28,r)}=1),1,0)')
        def rational(title, nums, dens, guard, enabled, places=0):
            nonlocal cursor
            ns,ds=list(nums),list(dens)
            def put(label,v,f):
                nonlocal cursor
                forecast.put(4,cursor,title+" · "+label); address=forecast.put(r,cursor,v,f); cursor+=1
                return address,v
            for j in range(len(ns)):
                for k in range(len(ds)):
                    nr,nv=ns[j]; dr,dv=ds[k]
                    g=gcd(nv,dv) if enabled else 1
                    gr,_=put("公因子",g,f'IF({guard},GCD({nr},{dr}),1)')
                    ns[j]=put("约分分子",nv//g if enabled else 0,f'IF({guard},{nr}/{gr},0)')
                    ds[k]=put("约分分母",dv//g if enabled else 1,f'IF({guard},{dr}/{gr},1)')
            num=den=1
            for _,v in ns:num*=v
            for _,v in ds:den*=v
            nr,_=put("分子积",num,f'PRODUCT({",".join(a for a,_ in ns)})'); dr,_=put("分母积",den,f'PRODUCT({",".join(a for a,_ in ds)})')
            valid=enabled and 0<=num<=INTEGER_LIMIT and 1<=den<=INTEGER_LIMIT
            vr,_=put("整数精度可用",int(valid),f'IF(AND({guard},{nr}>=0,{nr}<={INTEGER_LIMIT},{dr}>=1,{dr}<={INTEGER_LIMIT}),1,0)')
            result=rounded(Fraction(num,den))/10**places if valid else None
            if valid and not places: result=int(result)
            out,_=put("四舍五入结果",result,f'IF({vr},(QUOTIENT({nr},{dr})+IF({rem(nr, dr)}>={dr}-{rem(nr, dr)},1,0))/{10**places},"")')
            return out,result,vr
        gmv=rational("归因金额",[(f'B{r}',amounts[i]),(f'D{r}',facts.get("reportedGmvCents") or 0),(f'F{r}',a["orderRateFactorBps"]),(f'G{r}',a["orderValueFactorBps"])],[(f'C{r}',facts.get("spendCents") or 0),(f'E{r}',a["cpcFactorBps"]),("10000",10000)],f'I{r}',usable)
        forecast.put(r,10,gmv[1],f'IF({gmv[2]},{gmv[0]},"")')
        roas=rational("产出比",[(f'J{r}',gmv[1] or 0),("1000000",1000000)],[(f'B{r}',amounts[i])],f'AND(COUNT(J{r})=1,B{r}>0)',gmv[1] is not None and amounts[i]>0,6)
        forecast.put(r,11,roas[1],f'IF({roas[2]},{roas[0]},"")')
        contribution=rational("贡献",[(f'J{r}',gmv[1] or 0),(f'H{r}',a["contributionMarginBps"] or 0)],[("10000",10000)],f'COUNT(J{r},H{r})=2',gmv[1] is not None and a["contributionMarginBps"] is not None)
        cv=contribution[1]-amounts[i] if contribution[1] is not None else None
        forecast.put(r,12,cv,f'IF({contribution[2]},{contribution[0]}-B{r},"")')
        review=rational("复盘花费",[(f'B{r}',amounts[i]),(f'ROUND({mr(9)}*100,0)',plan["reviewAfterSpendBps"])],[("10000",10000)],ar(8,1),True)
        forecast.put(r,13,review[1],f'IF({review[2]},{review[0]},"")')
        normok=facts.get("spendCents") is not None and facts["spendCents"]>=0 and b["datesPresent"]
        norm=rational("基期等天数",[(f'C{r}',facts.get("spendCents") or 0),(mr(7),plan["horizonDays"])],[(ar(10,r),b["days"])],f'AND({ar(8,1)},COUNT(C{r})=1,C{r}>=0,{ar(11,r)}=1)',normok)
        forecast.put(r,14,norm[1],f'IF({norm[2]},{norm[0]},"")'); forecast.put(r,15,amounts[i]-norm[1] if norm[1] is not None else None,f'IF(COUNT(B{r},N{r})=2,B{r}-N{r},"")')
        baseline_status = "基期对比不可测算" if not normok else "基期对比可测算" if norm[1] is not None else "基期对比需高精度复算"
        forecast.put(r,21,baseline_status,f'IF(NOT({ar(8,1)}),"参数无效",IF(NOT(AND(COUNT(C{r})=1,C{r}>=0,{ar(11,r)}=1)),"基期对比不可测算",IF(COUNT(N{r})=1,"基期对比可测算","基期对比需高精度复算")))')
        precision=gmv[1] is not None and (a["contributionMarginBps"] is None or cv is not None) and (not amounts[i] or roas[1] is not None)
        status="不可测算" if not usable else "需高精度复算" if not precision else "低样本假设" if expected_row["status"]=="low_sample_scenario" else "假设情景"
        if baseline_status == "基期对比需高精度复算": status += "；"+baseline_status
        forecast.put(r,20,int(precision),f'IF(AND(COUNT(J{r})=1,OR(H{r}="",COUNT(L{r})=1),OR(B{r}=0,COUNT(K{r})=1)),1,0)')
        forecast.put(r,16,status,f'IF(NOT({ar(8,1)}),"参数无效",IF(NOT(I{r}),"不可测算",IF(NOT(T{r}),"需高精度复算",IF({ar(29,r)},"低样本假设","假设情景"))))&IF(U{r}="基期对比需高精度复算","；"&U{r},"")')
        for c,v,f in ((17,plan["observationDays"],mr(8)),(18,t["ownerRole"],ar(30,r)),(19,t["minimumRoasBps"]/10000,ar(5,r))): forecast.put(r,c,v,f)
        for key,actual in (("projectedAttributedGmvCents",gmv[1]),("projectedRoas",roas[1]),("assumedContributionAfterAdCents",cv),("reviewAfterSpendCents",review[1]),("equivalentBaselineSpendCents",norm[1])):
            if actual is not None and actual!=expected_row[key]: raise AnalysisContractError("Excel 情景初值与同源结果不一致")
        outputs.append((gmv[1],cv,status))
    for r,label,col in ((5,"已分配预算（元）",27),(6,"未分配余额（元）",None)):
        main.put(r,4,label); main.put(r,5,sum(amounts)/100 if col else (available-sum(amounts))/100,f'IF({ar(8,1)},({"SUM("+alloc_range(27)+")" if col else ar(2,1)+"-SUM("+alloc_range(27)+")"})/100,"")')
    for r,label,col,pos in ((7,"情景归因金额（元）",10,0),(8,"假设贡献扣推广余额（元）",12,1)):
        main.put(r,4,label)
        total_ok = same_basis and all(o[pos] is not None for o in outputs) and sum(abs(o[pos]) for o in outputs) <= INTEGER_LIMIT
        total=sum(o[pos] for o in outputs)/100 if total_ok else "不可合计或不可测算"
        span = forecast_range(col)
        main.put(r,5,total,f'IF(NOT({ar(8,1)}),"参数无效",IF(AND({ar(8,2)},COUNT({span})={n},SUMIF({span},">0")-SUMIF({span},"<0")<={INTEGER_LIMIT}),SUM({span})/100,"不可合计或不可测算"))')
    main.put(10,4,"输入状态"); main.put(10,5,"参数有效",f'IF({ar(8,1)},"参数有效","请检查参数及最低预算合计")')
    main.put(11,4,"当前情景"); main.put(11,5,a["name"],f'IF({ar(8,1)},{selected(2)},"参数无效")')
    main.put(23,1,"贡献率是输入假设，扣推广余额不是实际利润。不同平台成交口径不合计。")
    main.put(24,1,"修改不更新原报告结论；对象权重和上下限在“预算分配计算”修改。")
    for c,label in enumerate(("对象","预算（元）","情景归因金额（元）","归因产出比","假设贡献余额（元）","提前复盘花费（元）","观察天数","状态"),1): main.header(26,c,label)
    for i,(gmv,cv,status) in enumerate(outputs):
        r=i+5; row=i+27
        main.row_heights[row] = 56
        for c,source,scale in ((1,1,1),(2,2,100),(3,10,100),(4,11,1),(5,12,100),(6,13,100),(7,17,1),(8,16,1)):
            v=forecast.cells[r,source][0]
            f=fr(source,r)
            formula=f'IF(COUNT({f})=1,{f}/{scale},"")' if c not in (1,8) else f
            main.put(row,c,v/scale if type(v) in (int,float) else v,formula)
    return sheets,{"schemaVersion":"business-excel-budget-v1","reportId":value["reportId"],"planDigest":value["planDigest"],"sheets":list(names),"activeScenario":1,
        "initialUnmeasurableTargets":sum(o[0] is None for o in outputs),"integerProductLimit":INTEGER_LIMIT,"reviewStatus":"unreviewed_local_scenario"}
