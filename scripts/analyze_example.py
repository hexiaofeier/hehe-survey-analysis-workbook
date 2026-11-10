"""Recompute the synthetic example with Python 3.10+, standard library only.

Run: python scripts/analyze_example.py --output recomputed
Reads the bundled CSV; never modifies it or replaces the example workbook.
"""
from pathlib import Path
import argparse, csv, json, math

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/robot-vacuum'
GROUPS = [('总体', lambda r: True), ('养宠家庭', lambda r: r['pet'] == '1'),
          ('无宠家庭', lambda r: r['pet'] == '0'), ('每周1—2天', lambda r: r['frequency']=='1'), ('每周3—4天', lambda r: r['frequency']=='2'), ('每周5—7天', lambda r: r['frequency']=='3')]
QUESTIONS = [
    ('Q1', 'pet', '家中是否养宠物？', [('1', '养宠'), ('0', '无宠')], 'single', '家庭与使用'),
    ('Q2', 'frequency', '过去一周使用扫地机器人的天数？', [('1', '1—2天'), ('2', '3—4天'), ('3', '5—7天')], 'single', '家庭与使用'),
    ('Q3', 'problems', '过去一个月遇到过哪些问题？（多选）', [('hair', '毛发缠绕'), ('edge', '边角清洁不净'), ('noise', '噪音影响'), ('wash', '清洗维护费时')], 'multi', '家庭与使用'),
    ('Q4', 'satisfaction', '对现用机器人的总体满意度？', [(str(i), s) for i, s in zip(range(1,6), ['非常不满意','比较不满意','一般','比较满意','非常满意'])], 'scale', '满意度与推荐'),
    ('Q5', 'support', '过去半年是否联系过售后？', [('1','联系过'),('0','没有')], 'single', '满意度与推荐'),
    ('Q6', 'support_score', '对最近一次售后服务的满意度？', [(str(i), s) for i, s in zip(range(1,6), ['非常不满意','比较不满意','一般','比较满意','非常满意'])], 'scale', '满意度与推荐'),
    ('Q7', 'recommend', '向亲友推荐现用机器人的意愿？', [(str(i),str(i)+'分') for i in range(11)], 'nps', '满意度与推荐'),
    ('Q8', 'before', '体验前对新产品的购买兴趣？', [(str(i),str(i)+'分') for i in range(1,6)], 'scale', '新品体验'),
    ('Q9', 'after', '体验后对新产品的购买兴趣？', [(str(i),str(i)+'分') for i in range(1,6)], 'scale', '新品体验'),
]

def quality(rows):
    marked=[]
    for r in rows:
        missing=[]
        for qid,field,title,options,kind,sheet in QUESTIONS:
            eligible=field!='support_score' or r['support']=='1'
            if eligible and (r['problems_answered']=='0' if kind=='multi' else r[field]==''):
                missing.append(qid)
        marked.append({'id':r['id'],'quality':'证据不足' if missing else '合格',
            'flag':'异常' if missing else '正常','reason':'部分应答题未答：'+ '、'.join(missing) if missing else '已具备字段未发现异常',
            'evidence':'、'.join(missing)+'为空' if missing else 'ID唯一、取值与已有跳题一致',
            'agent_recommendation':'纳入，逐题使用有效回答', 'user_decision':'纳入（合成示例预设）'})
    return marked


def analyze():
    with (EXAMPLE/'responses.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    results, blocks = [], []
    for qid, field, title, options, kind, sheet in QUESTIONS:
        block = {'qid':qid,'field':field,'title':title,'kind':kind,'sheet':sheet,'rows':[],'bases':[]}
        valid_by_group=[]
        for group, pred in GROUPS:
            valid=[r for r in rows if pred(r) and (r['problems_answered']=='1' if kind=='multi' else r[field]!='')]
            valid_by_group.append(valid); block['bases'].append(len(valid))
        specs=[(label,'proportion',code) for code,label in options]
        if kind=='scale':specs += [('TOP2','top2',None),('均值','mean',None)]
        if kind=='nps':specs += [('NPS','nps',None)]
        for label, metric, code in specs:
            ids=[]
            for (group,_),valid in zip(GROUPS,valid_by_group):
                n=len(valid)
                if metric=='mean':num=sum(int(r[field]) for r in valid)
                elif metric=='top2':num=sum(int(r[field])>=4 for r in valid)
                elif metric=='nps':num=sum(int(r[field])>=9 for r in valid)-sum(int(r[field])<=6 for r in valid)
                else:num=sum(r[code]=='1' if kind=='multi' else r[field]==code for r in valid)
                result=num/n*(100 if metric=='nps' else 1) if n else None
                rid=f'R{len(results)+1:04d}'; ids.append(rid)
                results.append({'id':rid,'qid':qid,'field':field,'metric':metric,'option':label,'group':group,'numerator':num,'base':n,'result':result})
            block['rows'].append({'label':label,'metric':metric,'ids':ids})
        blocks.append(block)
    # Locations refer directly to visible question tables; the index stays outside Excel.
    lookup={r['id']:r for r in results}; next_row={}
    for block in blocks:
        start=next_row.get(block['sheet'],2)
        for offset,item in enumerate(block['rows']):
            for group_index,result_id in enumerate(item['ids']):
                lookup[result_id]['location']=f"{block['sheet']}!{chr(67+group_index)}{start+offset}"
                lookup[result_id]['source_version']='synthetic-v1'
        next_row[block['sheet']]=start+len(block['rows'])+5
    # A paired binary comparison: interest TOP2 before / after the SAME experience.
    pairs=[r for r in rows if r['before'] and r['after']]
    up=sum(int(r['before'])<4 and int(r['after'])>=4 for r in pairs)
    down=sum(int(r['before'])>=4 and int(r['after'])<4 for r in pairs)
    discordant=up+down
    p=min(1.,2*sum(math.comb(discordant,k) for k in range(min(up,down)+1))/(2**discordant)) if discordant else 1.
    # Paired proportion-change normal interval uses per-person difference variance.
    diffs=[int(int(r['after'])>=4)-int(int(r['before'])>=4) for r in pairs]
    delta=sum(diffs)/len(diffs)
    var=sum((d-delta)**2 for d in diffs)/(len(diffs)-1)
    se=(var/len(diffs))**.5
    paired={'pairs':len(pairs),'up':up,'down':down,'before_top2':sum(int(r['before'])>=4 for r in pairs)/len(pairs),
            'after_top2':sum(int(r['after'])>=4 for r in pairs)/len(pairs),'difference':delta,
            'low':max(-1,delta-1.959963984540054*se),'high':min(1,delta+1.959963984540054*se),
            'p_exact':p,'method':'McNemar精确双侧；差值区间为配对差值正态近似95%区间；仅1项预设检验'}
    qc={'rows':len(rows),'unique_ids':len({r['id'] for r in rows}),
        'multi_missing':sum(r['problems_answered']=='0' for r in rows),
        'support_eligible':sum(r['support']=='1' for r in rows),
        'support_missing':sum(r['support']=='1' and not r['support_score'] for r in rows),
        'support_routing_errors':sum(r['support']=='0' and r['support_score']!='' for r in rows),
        'pair_missing':len(rows)-len(pairs)}
    return {'synthetic':True,'sample_version':'synthetic-v1','groups':[g for g,_ in GROUPS],
            'blocks':blocks,'results':results,'paired':paired,'qc':qc,'quality':quality(rows)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();data=analyze();args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'analysis.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (args.output/'results.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(data['results'][0]));writer.writeheader();writer.writerows(data['results'])
    with (args.output/'results.jsonl').open('w',encoding='utf-8') as f:
        for row in data['results']:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(f"Recomputed {len(data['results'])} results from {data['qc']['rows']} synthetic responses.")

if __name__=='__main__':main()
