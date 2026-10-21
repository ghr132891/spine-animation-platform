"""Publish this test's actual saved outputs, never credentials or invented art."""
import argparse,json,re,shutil,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--task',required=True);parser.add_argument('--output',required=True,type=Path);a=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{32}',a.task):parser.error('Invalid task id')
    folder=ROOT/'storage/agent-tasks'/a.task;t=json.loads((folder/'task.json').read_text());out=a.output;out.mkdir(parents=True,exist_ok=True)
    images=[];jobs=[]
    labels={'character':'角色原图','view':'侧向准备图','turnaround':'三视图参考','character-assets':'完整部件图','costume':'完整衣装补片'}
    for cid in t['owned'].get('character',[]):
        for path in sorted((ROOT/'storage/characters'/cid/'jobs').glob('*.json'),key=lambda p:json.loads(p.read_text()).get('created_at',0)):
            job=json.loads(path.read_text());name=job.get('sheet') or job.get('mode','character')
            record={k:job.get(k) for k in ('id','gen_id','status','candidate_ready','created_at','error','failure') if k in job}
            record.update(label=labels.get(name,name),character_id=cid);jobs.append(record)
            source=path.with_suffix('.png')
            if source.exists() and job.get('candidate_ready'):
                filename='generated-'+job['id']+'.png';shutil.copy2(source,out/filename);images.append({'label':record['label'],'file':filename,'job_id':job['id']})
    for cid,record in t.get('material_preparation',{}).items():
        digest=record.get('snapshot_digest',record.get('digest'))
        if not digest:continue
        for panel in record.get('panels',[]):
            name=panel['name'];source=folder/'materials'/cid/digest/(name+'.png')
            if not source.exists():continue
            filename='material-'+cid+'-'+digest[:12]+'-'+name+'.png';shutil.copy2(source,out/filename)
            images.append({'label':{'parts':'完整部件验收','assembly':'试拼','spread':'双向摆开','edges':'边缘放大检查'}.get(name,name),'file':filename})
    reservations=t.get('reservations',{});artifacts=t.get('artifacts') or t.get('draft_artifacts',[])
    status={'task_id':t['id'],'state':t['state'],'stage':t.get('stage'),'stage_detail':t.get('stage_detail'),'summary':t.get('summary'),'error':t.get('error'),
            'jobs':jobs,'images':images,'created_at':t['created_at'],'updated_at':time.time(),'elapsed_seconds':round(time.time()-t['created_at']),
            'total_images_reserved':sum(r.get('image_count',r['count']) for r in reservations.values()),
            'additional_reserved':sum(r['count'] for r in reservations.values()),'additional_limit':t['max_generations'],
            'artifacts':[{k:r.get(k) for k in ('kind','id','preview_url','download_url','summary')} for r in artifacts],
            'prebind':[{'character_id':cid,'status':r.get('status'),'message':r.get('message')} for cid,r in t.get('material_preparation',{}).items()]}
    (out/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>橙衣快递员 · 全流程实测</title><style>body{max-width:1080px;margin:30px auto;padding:0 22px;background:#edf2f7;color:#243449;font:16px/1.7 system-ui}section,figure{background:white;border-radius:12px;padding:22px;margin:20px 0}img{max-width:100%;max-height:1000px;object-fit:contain}a{color:#2458a6}pre{white-space:pre-wrap}</style><h1>橙衣快递员 · 从新图到 Spine 动画</h1><p>从网页描述生成全新人物，依次准备三视图和完整部件、检查与装配、制作待机和行走、复检与交付。所有图像均来自本次任务实际保存的产物。</p><p><a id="task">查看原网页任务</a> · <a href="status.json">阶段记录</a></p><section><h2 id="state"></h2><p id="budget"></p><p id="summary"></p><div id="artifacts"></div></section><div id="images"></div><script>
let signature='';async function refresh(){const r=await fetch('status.json?t='+Date.now(),{cache:'no-store'});const s=await r.json(),next=JSON.stringify(s);if(signature===next)return;signature=next;document.getElementById('task').href='/auto.html?task='+s.task_id;document.getElementById('state').textContent=({queued:'排队中',running:'正在制作',waiting_reference:'等待参考图通道恢复',completed:'制作完成',failed:'本轮未完成，保留已有结果',interrupted:'制作中断，保留已有结果'})[s.state]||s.state;document.getElementById('budget').textContent='已提交／预留图片 '+s.total_images_reserved+' 张；补图与修订额度 '+s.additional_reserved+'/'+s.additional_limit+'（首次三视图和部件图另计）。';document.getElementById('summary').textContent=s.summary||s.error||'按新流程检查和处理当前素材，准备完成不代表验收通过。';const outputs=document.getElementById('artifacts');outputs.replaceChildren();for(const a of s.artifacts){if(a.preview_url){const link=document.createElement('a');link.href=a.preview_url;link.textContent=s.state==='completed'?'播放最终动画':'播放当前草稿';outputs.append(link)}}const images=document.getElementById('images');images.replaceChildren();for(const p of s.images){const f=document.createElement('figure'),caption=document.createElement('figcaption'),link=document.createElement('a'),im=document.createElement('img');caption.textContent=p.label;link.href=p.file;im.src=p.file;im.alt=p.label;im.loading='lazy';link.append(im);f.append(caption,link);images.append(f)}}refresh();setInterval(refresh,15000);
</script></html>'''
    (out/'index.html').write_text(html)
    print(json.dumps({'state':status['state'],'stage':status['stage'],'images':len(images),'jobs':[(j['label'],j['status']) for j in jobs]},ensure_ascii=False))

if __name__=='__main__':main()
