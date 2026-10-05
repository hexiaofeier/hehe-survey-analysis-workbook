"""Validate package and independently check the bundled synthetic workbook.
Python 3.10+ standard library; no network, uploads, or writes.
"""
from pathlib import Path
from urllib.parse import unquote
from collections import Counter
import csv, hashlib, json, math, re, sys, zipfile
import xml.etree.ElementTree as ET

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
NAME='hehe-survey-analysis-workbook'
SKILL=ROOT/'skills'/NAME
ERRORS=[]
def check(ok,msg):
    if not ok:ERRORS.append(msg)
def read(p):return p.read_text(encoding='utf-8-sig')

def workbook_cells(p):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    with zipfile.ZipFile(p) as z:
        check(z.testzip() is None,'Workbook ZIP corruption')
        check(not any(x.startswith('xl/charts/') for x in z.namelist()),'Unexpected charts')
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels={x.attrib['Id']:x.attrib['Target'] for x in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheets={}
        for s in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns):
            target=rels[s.attrib['{'+ns['r']+'}id']]
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            xml=ET.fromstring(z.read(target))
            check(not xml.findall('s:mergeCells/s:mergeCell',ns),'Merged data cells: '+s.attrib['name'])
            cells={}
            for c in xml.findall('.//s:sheetData/s:row/s:c',ns):
                t=c.attrib.get('t');v=c.find('s:v',ns)
                if t=='s':value=strings[int(v.text)] if v is not None else ''
                elif t=='inlineStr':value=''.join(x.text or '' for x in c.findall('.//s:t',ns))
                elif v is None:value=''
                elif t in ['str','e']:value=v.text or ''
                else:value=float(v.text) if v.text else ''
                check(t!='e','Excel error: '+s.attrib['name']+'!'+c.attrib['r'])
                cells[c.attrib['r']]=value
            sheets[s.attrib['name']]=cells
        return sheets

def main():
    manifest=json.loads(read(ROOT/'release-manifest.json'))
    check(manifest['entry_skill']==NAME,'Wrong entry Skill')
    check(manifest['required_skills']==[],'Unexpected required Skill')
    check(manifest['install_policy']=='standalone','Wrong install policy')
    check(re.search(r'^name: '+NAME+r'$',read(SKILL/'SKILL.md'),re.M),'Wrong frontmatter name')
    check('$'+NAME in read(SKILL/'agents/openai.yaml'),'Wrong invocation name')
    check(sorted(p.name for p in (ROOT/'skills').iterdir())==[NAME],'Unexpected install folder')
    for f in manifest['required_local_files']:
        p=SKILL/f;check(p.is_file(),'Missing runtime: '+f)
        if p.exists():check(hashlib.sha256(p.read_bytes()).hexdigest()==manifest['public_sha256'][f],'Hash mismatch: '+f)
    check({p.relative_to(SKILL).as_posix() for p in SKILL.rglob('*') if p.is_file()}==set(manifest['required_local_files']),'Unexpected runtime files')
    allowed_top={'skills','scripts','docs','examples','README.md','LICENSE','release-manifest.json','.gitignore','.gitattributes'}
    for p in ROOT.rglob('*'):
        rel=p.relative_to(ROOT)
        if '.git' in rel.parts:continue
        check(rel.parts[0] in allowed_top,'Unexpected package entry: '+str(rel))
        check(not any(x in {'_本地维护','90_历史备份','node_modules','__pycache__'} for x in rel.parts),'Private/cache folder: '+str(rel))
        if not p.is_file():continue
        check('_CO' not in p.name,'Local filename: '+str(rel))
        check(p.suffix.lower() not in {'.sav','.zsav','.env','.log'},'Private/raw format: '+str(rel))
        if p.suffix not in {'.md','.json','.yaml','.yml','.py','.csv','.svg'}:continue
        t=read(p)
        if p.name!='validate_release.py':
            check(re.search(r'[A-Z]:[\\/](?:Users|LenovoSoftstore)[\\/]',t,re.I) is None,'Machine path: '+str(rel))
            check(re.search(r'(?<![\w-])(?:tanzi-pro|agentkey)(?![\w-])',t,re.I) is None,'Private dependency: '+str(rel))
            check(re.search(r'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{30,}',t) is None,'Possible credential: '+str(rel))
        if p.is_relative_to(SKILL):check(re.search(r'(?<![a-z0-9-])survey-analysis-workbook(?![a-z0-9-])',t) is None,'Old runtime name')
        if p.suffix=='.md':
            for u in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',t):
                if u.startswith('#') or re.match(r'^[a-z]+:',u,re.I):continue
                dst=(p.parent/unquote(u.strip('<>').split('#')[0])).resolve()
                check(dst.is_relative_to(ROOT.resolve()) and dst.exists(),'Broken link: '+str(rel)+' -> '+u)
                if p.is_relative_to(SKILL):check(dst.is_relative_to(SKILL.resolve()),'Runtime link outside Skill')
    ex=ROOT/'examples/robot-vacuum'
    with (ex/'responses.csv').open(encoding='utf-8-sig',newline='') as f:raw=list(csv.DictReader(f))
    check(len(raw)==120 and len({r['id'] for r in raw})==120,'Synthetic sample size/IDs')
    check(all(re.fullmatch(r'SIM\d{3}',r['id']) for r in raw),'Non-synthetic ID')
    cells=workbook_cells(ex/'survey-analysis.xlsx')
    check(len(cells)==8,'Wrong sheet count')
    from analyze_example import analyze, QUESTIONS
    result=analyze()
    # Independently aggregate each question/group from raw CSV (not the output list).
    expectation={}
    for qid,field,title,options,kind,sheet in QUESTIONS:
        for group in ['总体','养宠家庭','无宠家庭']:
            subset=[r for r in raw if group=='总体' or r['pet']==('1' if group=='养宠家庭' else '0')]
            valid=[r for r in subset if (r['problems_answered']=='1' if kind=='multi' else r[field]!='')]
            n=len(valid)
            freq=Counter(r[field] for r in valid) if kind!='multi' else Counter({key:sum(int(r[key]) for r in valid) for key,_ in options})
            for code,label in options:expectation[(qid,group,'proportion',label)]=(freq[code],n)
            if kind=='scale':
                expectation[(qid,group,'top2','TOP2')]=(sum(freq[str(k)] for k in [4,5]),n)
                expectation[(qid,group,'mean','均值')]=(sum(int(k)*v for k,v in freq.items()),n)
            if kind=='nps':expectation[(qid,group,'nps','NPS')]=(sum(freq[str(k)] for k in [9,10])-sum(freq[str(k)] for k in range(7)),n)
    ix=cells['结果索引']
    for i,r in enumerate(result['results'],5):
        num,n=expectation[(r['qid'],r['group'],r['metric'],r['option'])]
        v=num/n*(100 if r['metric']=='nps' else 1) if n else None
        check(r['numerator']==num and r['base']==n,'Script count mismatch: '+r['id'])
        check(ix.get(f'E{i}')==num and ix.get(f'F{i}')==n,'Workbook count mismatch: '+r['id'])
        check(isinstance(ix.get(f'H{i}'),(int,float)) and math.isclose(ix[f'H{i}'],v,abs_tol=1e-10),'Index value: '+r['id'])
        sheet,addr=ix[f'L{i}'].split('!')
        check(math.isclose(cells[sheet][addr],v,abs_tol=1e-10),'Displayed value: '+r['id'])
    # Paired test and near-zero p-value format: numeric cached values remain precise.
    paired=[(int(r['before'])>=4,int(r['after'])>=4) for r in raw if r['before'] and r['after']]
    up=sum(not a and b for a,b in paired);down=sum(a and not b for a,b in paired)
    p=min(1,2*sum(math.comb(up+down,k)/2**(up+down) for k in range(min(up,down)+1)))
    test=cells['差异检验']
    check(test['B6']==len(paired) and test['B9']==up and test['B10']==down,'Paired counts')
    check(math.isclose(test['B14'],p,rel_tol=1e-10),'Paired p-value')
    # Every option distribution and sample row, including multi totals.
    total_checks=0
    for name in ['购买与使用','满意度与推荐']:
        table=cells[name]
        for b in [b for b in result['blocks'] if b['sheet']==name]:
            first=next(int(k[1:]) for k,v in table.items() if k.startswith('A') and v==b['qid']+' '+b['title'])
            totalrow=first+len(b['rows'])
            options=[r for r in b['rows'] if r['metric']=='proportion']
            for gi,g in enumerate(['总体','养宠家庭','无宠家庭']):
                letter=chr(67+gi);base=b['bases'][gi]
                num=sum(expectation[(b['qid'],g,'proportion',r['label'])][0] for r in options)
                check(math.isclose(table[f'{letter}{totalrow}'],num/base,abs_tol=1e-10),'Total mismatch: '+b['qid'])
                check(table[f'{letter}{totalrow+1}']==base,'Sample row mismatch: '+b['qid'])
                total_checks+=1
    if ERRORS:sys.exit('\n'.join('ERROR: '+e for e in ERRORS))
    print(f'PASS: standalone runtime, hashes and links; 120 synthetic records; {len(expectation)} statistics, {total_checks} totals/bases, paired test; 8-sheet XLSX integrity.')

if __name__=='__main__':main()
