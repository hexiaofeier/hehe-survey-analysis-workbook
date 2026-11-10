"""Independently check package and synthetic results. Python 3.10+, standard library.
Reads local files only. Does not upload, modify, or filter source data.
"""
from pathlib import Path
from urllib.parse import unquote,urlsplit
from collections import Counter
import csv,hashlib,json,math,re,sys,zipfile,xml.etree.ElementTree as E
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1];NAME='hehe-survey-analysis-workbook';SKILL=ROOT/'skills'/NAME
S='http://schemas.openxmlformats.org/spreadsheetml/2006/main';R='http://schemas.openxmlformats.org/officeDocument/2006/relationships';NS={'s':S}
ERRORS=[]
def check(ok,msg):
    if not ok:ERRORS.append(msg)
def eq(a,b,msg):check(isinstance(a,(int,float)) and math.isclose(a,b,abs_tol=1e-10,rel_tol=1e-10),msg)
def read(p):return p.read_text(encoding='utf-8-sig')
def workbook(path):
    with zipfile.ZipFile(path) as z:
        check(z.testzip() is None,'Corrupt workbook: '+path.name)
        check(not any(n.startswith('xl/charts/') for n in z.namelist()),'Unexpected charts')
        strings=[''.join(e.itertext()) for e in E.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        rels={e.attrib['Id']:e.attrib['Target'] for e in E.fromstring(z.read('xl/_rels/workbook.xml.rels'))};sheets={};links=[]
        for s in E.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',NS):
            target=rels[s.attrib['{'+R+'}id']];target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            xml=E.fromstring(z.read(target));cells={}
            check(not xml.findall('s:mergeCells/s:mergeCell',NS),'Merged data cells: '+s.attrib['name'])
            for c in xml.findall('.//s:sheetData/s:row/s:c',NS):
                k=c.attrib.get('t');v=c.find('s:v',NS)
                if k=='s':value=strings[int(v.text)]
                elif k=='inlineStr':value=''.join(t.text or '' for t in c.findall('.//s:t',NS))
                elif v is None:value=''
                elif k in {'str','e'}:value=v.text or ''
                else:value=float(v.text) if v.text else ''
                check(k!='e','Excel error: '+s.attrib['name']+'!'+c.attrib['r']);cells[c.attrib['r']]=value
            sheets[s.attrib['name']]=cells
            links += [(s.attrib['name'],l.attrib['ref'],l.attrib['location']) for l in xml.findall('s:hyperlinks/s:hyperlink',NS) if 'location' in l.attrib]
        for s,c,target in links:
            dest,addr=target.rsplit('!',1);dest=dest.strip("'")
            check(dest in sheets and addr.replace('$','') in sheets.get(dest,{}),'Broken workbook link: '+target)
        return sheets,links
def main():
    m=json.loads(read(ROOT/'release-manifest.json'))
    req={'SKILL.md','agents/openai.yaml','references/sample-cleaning-and-quality.md','references/statistical-methods.md','references/special-analysis-methods.md','references/workbook-output-spec.md','references/analysis-plan-template.md','references/html-brief.md','references/basic-statistics.md','scripts/render_brief.py','scripts/basic_statistics.py','assets/brief.html'}
    check(m['entry_skill']==NAME and m['required_skills']==[] and m['install_policy']=='standalone','Entry/install policy')
    check(set(m['required_local_files'])==req,'Manifest must include all 12 runtime resources')
    check({p.relative_to(SKILL).as_posix() for p in SKILL.rglob('*') if p.is_file()}==req,'Runtime scope')
    check(re.search(r'^name: '+NAME+'$',read(SKILL/'SKILL.md'),re.M),'Public frontmatter');check('$'+NAME in read(SKILL/'agents/openai.yaml'),'Invocation name')
    for f in req:check(hashlib.sha256((SKILL/f).read_bytes()).hexdigest()==m['public_sha256'][f],'Runtime hash: '+f)
    check([p.name for p in (ROOT/'skills').iterdir()]==[NAME],'Single install folder')
    allowed={'skills','scripts','docs','examples','README.md','LICENSE','release-manifest.json','.gitignore','.gitattributes'}
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if '.git' in rel.parts:continue
        check(rel.parts[0] in allowed,'Unexpected top-level entry: '+str(rel));check(not any(a in {'_本地维护','90_历史备份','node_modules','__pycache__'} for a in rel.parts),'Private/cache directory')
        if not p.is_file():continue
        check('_CO' not in p.name,'Local-only filename: '+str(rel));check(p.suffix.lower() not in {'.sav','.zsav','.env','.log'},'Private/raw file format')
        if p.suffix not in {'.md','.json','.yaml','.yml','.py','.csv','.svg','.html','.jsonl'}:continue
        text=read(p)
        if p.name!='validate_release.py':
            check(re.search(r'[A-Z]:[\\/](?:Users|LenovoSoftstore)[\\/]',text,re.I) is None,'Machine path: '+str(rel))
            check(re.search(r'(?<![\w-])(?:tanzi-pro|agentkey)(?![\w-])',text,re.I) is None,'Private dependency: '+str(rel))
            check(re.search(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{30,}',text) is None,'Possible credential')
        if p.is_relative_to(SKILL):check(re.search(r'(?<![a-z0-9-])survey-analysis-workbook(?![a-z0-9-])',text) is None,'Old runtime name')
        if p.suffix=='.md':
            for u in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
                if u.startswith('#') or re.match(r'^[a-z]+:',u,re.I):continue
                dst=(p.parent/unquote(u.strip('<>').split('#')[0])).resolve();check(dst.is_relative_to(ROOT.resolve()) and dst.exists(),'Broken Markdown link: '+str(rel)+' -> '+u)
                if p.is_relative_to(SKILL):check(dst.is_relative_to(SKILL.resolve()),'Reference outside runtime')
    ex=ROOT/'examples/robot-vacuum'
    with (ex/'responses.csv').open(encoding='utf-8-sig',newline='') as f:raw=list(csv.DictReader(f))
    check(len(raw)==120 and len({r['id'] for r in raw})==120 and all(re.fullmatch(r'SIM\d{3}',r['id']) for r in raw),'Synthetic records/IDs')
    cells,links=workbook(ex/'survey-analysis.xlsx');sample,_=workbook(ex/'sample-review.xlsx')
    check(list(cells)[:2]==['使用说明','分析方案'] and len(cells)==10 and all('索引' not in s for s in cells),'Sheet structure')
    check(len(links)==12,'Clickable contents and evidence links')
    from analyze_example import analyze,QUESTIONS,GROUPS
    actual=analyze();expect={};nchecks=0
    for q,field,title,options,kind,sheet in QUESTIONS:
        start=next((int(k[1:]) for k,v in cells[sheet].items() if k.startswith('A') and isinstance(v,str) and v.startswith(q+'. ')),None)
        check(start is not None,'Original question missing: '+q)
        if start is None:continue
        specs=[(label,'proportion',code) for code,label in options]
        if kind=='scale':specs += [('TOP2','top2',None),('均值','mean',None)]
        if kind=='nps':specs += [('NPS','nps',None)]
        for g,(group,_) in enumerate(GROUPS):
            subset=raw if g==0 else [r for r in raw if r['pet']==str(2-g)] if g in {1,2} else [r for r in raw if r['frequency']==str(g-2)]
            valid=[r for r in subset if (r['problems_answered']=='1' if kind=='multi' else r[field]!='') and (field!='support_score' or r['support']=='1')];n=len(valid);total=0;col=chr(67+g)
            for i,(label,metric,code) in enumerate(specs):
                if metric=='proportion':num=sum((r[code]=='1' if kind=='multi' else r[field]==code) for r in valid)
                elif metric=='top2':num=sum(int(r[field])>=4 for r in valid)
                elif metric=='mean':num=sum(int(r[field]) for r in valid)
                else:num=sum(int(r[field])>=9 for r in valid)-sum(int(r[field])<=6 for r in valid)
                value=num/n*(100 if metric=='nps' else 1);expect[(q,group,metric,label)]=(num,n,value);row=start+i
                check(cells[sheet].get('B'+str(row))==label,'Option order: '+q);eq(cells[sheet].get(col+str(row)),value,f'Result: {sheet}!{col}{row}')
                if metric=='proportion':total+=value
                nchecks+=1
            nr=start+len(specs)+1;eq(cells[sheet].get(col+str(nr-1)),total,'Total: '+q+'/'+group);eq(cells[sheet].get(col+str(nr)),n,'Sample: '+q+'/'+group);check(cells[sheet].get('B'+str(nr))=='样本量','Sample label')
    for a in actual['results']:
        num,n,value=expect[(a['qid'],a['group'],a['metric'],a['option'])];check(a['numerator']==num and a['base']==n,'Script numerator/base');eq(a['result'],value,'Script result')
        dest,addr=a['location'].split('!');eq(cells[dest].get(addr),value,'Backend result location: '+a['id'])
    pairs=[(int(r['before'])>=4,int(r['after'])>=4) for r in raw if r['before'] and r['after']];up=sum(not a and b for a,b in pairs);down=sum(a and not b for a,b in pairs)
    p=min(1,2*sum(math.comb(up+down,k)/2**(up+down) for k in range(min(up,down)+1)));ds=[int(b)-int(a) for a,b in pairs];delta=sum(ds)/len(ds);se=(sum((d-delta)**2 for d in ds)/(len(ds)-1)/len(ds))**.5
    for i,v in enumerate([len(pairs),sum(a for a,b in pairs)/len(pairs),sum(b for a,b in pairs)/len(pairs),up,down,delta,max(-1,delta-1.959963984540054*se),min(1,delta+1.959963984540054*se),p],2):eq(cells['配对比较'].get('B'+str(i)),v,'Paired statistic '+str(i))
    marked=sample['全量标记样本'];qc=Counter()
    for i,r in enumerate(raw,2):
        for j,(key,value) in enumerate(r.items()):check(marked.get(chr(65+j)+str(i),'')==(value if key=='id' or value=='' else float(value)),'Original field changed: '+r['id']+'/'+key)
        missing=[q for q,f,t,o,kind,sh in QUESTIONS if (f!='support_score' or r['support']=='1') and (r['problems_answered']=='0' if kind=='multi' else r[f]=='')]
        status='证据不足' if missing else '合格';qc[status]+=1
        check(marked.get('O'+str(i))==status and marked.get('P'+str(i))==('异常' if missing else '正常'),'Quality: '+r['id'])
        check(marked.get('S'+str(i))=='纳入，逐题使用有效回答' and marked.get('T'+str(i))=='纳入（合成示例预设）','Separate recommendation/decision')
    check(len([k for k in marked if re.fullmatch(r'A\d+',k) and marked[k] not in ['',None]])==121,'All source records retained')
    eq(cells['数据质量'].get('B4'),qc['证据不足'],'Insufficient count');eq(cells['数据质量'].get('B5'),qc['合格'],'Qualified count')
    qualified={a['id'] for a in actual['quality'] if a['quality']=='合格'}
    for row,field,cut in [(14,'hair',lambda x:x=='1'),(15,'satisfaction',lambda x:int(x)>=4),(16,'after',lambda x:int(x)>=4)]:
        group_values=[]
        for ids in [None,qualified]:
            eligible=[r for r in raw if (ids is None or r['id'] in ids) and r[field]!='' and (field!='hair' or r['problems_answered']=='1')]
            group_values.append(sum(cut(r[field]) for r in eligible)/len(eligible))
        for col,value in zip(['B','C','D'],[*group_values,(group_values[1]-group_values[0])*100]):eq(cells['数据质量'].get(col+str(row)),value,'Quality sensitivity: '+str(row))
    row=2
    for freq in ['1','2','3']:
        for pet in ['1','0']:
            sub=[r for r in raw if r['frequency']==freq and r['pet']==pet];h=[r for r in sub if r['problems_answered']=='1'];s=[r for r in sub if r['satisfaction']]
            vals=[sum(r['hair']=='1' for r in h),len(h),sum(r['hair']=='1' for r in h)/len(h),sum(int(r['satisfaction'])>=4 for r in s),len(s),sum(int(r['satisfaction'])>=4 for r in s)/len(s)]
            for j,v in enumerate(vals,2):eq(cells['分组差异追查'].get(chr(65+j)+str(row)),v,'Layered comparison '+str(row))
            row+=1
    html=read(ex/'analysis-brief.html');md=read(ex/'analysis-brief.md');ids=re.findall(r'\bid="([^"]+)"',html)
    check('id="sidebar"' in html and 'id="toc"' in html and len(ids)==len(set(ids)),'HTML sidebar/unique anchors')
    for href in re.findall(r'href="([^"]+)"',html):
        if href.startswith('#'):check(unquote(href[1:]) in ids,'Broken HTML anchor')
        elif not urlsplit(href).scheme:check((ex/unquote(href.split('#')[0])).exists(),'Broken HTML attachment')
    check(re.search(r'<(?:script|link)\b[^>]*(?:src|href)="https?://',html) is None,'Remote HTML dependency')
    for v in ['51.7%','48.2%','71.9%','24.6%','114','31','25.4']:check(v in md and v in html,'Brief number: '+v)
    check(nchecks==306,'Full question/group coverage')
    print(json.dumps({'passed':not ERRORS,'numeric_result_checks':nchecks,'records_preserved':len(raw),'analysis_sheets':len(cells),'sample_sheets':len(sample),'quality_counts':dict(qc),'runtime_files':len(req),'errors':ERRORS},ensure_ascii=False,indent=2))
    return 0 if not ERRORS else 1
if __name__=='__main__':raise SystemExit(main())
