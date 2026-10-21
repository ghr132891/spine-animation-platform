"""Check real prebind reuse without changing the source character or calling AI."""
import argparse,copy,json,shutil,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.character import CharacterProject
from app.core import png_bytes,sha
from app.prebind import inspect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--character',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    import re
    if not re.fullmatch('[a-f0-9]{32}',args.character):parser.error('Invalid character id')
    source=ROOT/'storage/characters'/args.character
    original=(source/'character.json').read_bytes()
    output=args.output;output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder=Path(tmp)/args.character
        shutil.copytree(source,folder,ignore=shutil.ignore_patterns('prebind-cache','jobs','versions'))
        c=CharacterProject(folder)
        started=time.perf_counter();baseline,expected=inspect(c);baseline_s=time.perf_counter()-started
        order=c.review_order([p['name'] for p in c.parts()])
        c.update({'revision':c.meta['revision'],'landmarks':copy.deepcopy(c.meta['landmarks']),'settings':c.meta['settings']})
        assert c.review_order([p['name'] for p in c.parts()])==order
        started=time.perf_counter();report,panels=c.prebind_evidence();cold=time.perf_counter()-started
        timings=[]
        for _ in range(3):
            started=time.perf_counter();cached,images=CharacterProject(folder).prebind_evidence();timings.append(time.perf_counter()-started)
            assert cached==report
            for name in panels:assert png_bytes(images[name])==png_bytes(panels[name])
        equality={name:png_bytes(expected[name])==png_bytes(image) for name,image in panels.items()}
        assert all(equality.values()) and report==baseline
        for name,image in panels.items():(output/(name+'.png')).write_bytes(png_bytes(image))
        result={'character_id':args.character,'source_metadata_unchanged':(source/'character.json').read_bytes()==original,
                'guide_save_preserves_order':True,'pixel_identical_to_uncached':equality,
                'baseline_render_seconds':round(baseline_s,4),'cold_prepare_seconds':round(cold,4),
                'cached_read_seconds':[round(t,4) for t in timings],
                'repeat_read_speedup':round(baseline_s/(sum(timings)/len(timings)),2),
                'prebind':report,'new_generation_calls':0,'visual_verdict':'not_reviewed',
                'panels':{name:sha(png_bytes(image)) for name,image in panels.items()}}
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
