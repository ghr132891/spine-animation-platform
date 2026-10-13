"""One authorized Flare smoke test; unknown submissions are never resubmitted."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import UserError, atomic_json, png_bytes, read_image, sha
from app.provider import Matrix, DEFAULT_MODEL

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--generate', action='store_true')
    args = parser.parse_args()
    out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    state_path = out / 'validation.json'
    if state_path.exists():
        raise SystemExit('A validation record already exists; inspect it instead of submitting again.')
    image = read_image(Path(args.image).read_bytes())
    normalized = png_bytes(image)
    (out / 'source.png').write_bytes(normalized)
    publisher = Matrix(out / 'cache')
    generator = Matrix(ROOT / 'storage')
    state = {'model': DEFAULT_MODEL, 'status': 'publishing', 'created_at': time.time(),
             'source_path': str(Path(args.image).resolve()), 'image_id': sha(normalized),
             'generation_calls': 0, 'spine_tasks_created': 0}
    atomic_json(state_path, state)
    try:
        publisher.tunnel.enable()
        record = publisher.ensure_reference(image)
        state.update(status='reference_verified', reference=record, exact_pixels_verified=True)
        origin = urllib.parse.urlsplit(record['url'])
        root_url = origin.scheme + '://' + origin.netloc
        checks = {}
        for path in ('/', '/api/health', '/storage/credentials.json'):
            try:
                with urllib.request.urlopen(root_url + path, timeout=15) as response:
                    checks[path] = response.status
            except urllib.error.HTTPError as error:
                checks[path] = error.code
        state['public_route_checks'] = checks
        if any(value != 404 for value in checks.values()):
            raise UserError('Unexpected public origin routes; generation cancelled.')
        atomic_json(state_path, state)
        print(json.dumps({'status': 'reference_verified', 'exact_pixels': True, 'isolated_routes': checks}), flush=True)
        if not args.generate:
            return
        prompt = ('Use the supplied reference image. Keep this same single male chibi warrior, '
                  'his stern face and beard, topknot and blue hairband, blue coat, burgundy collar, '
                  'black belt, white sleeve cuffs, dark patched trousers, gray shin wraps and brown shoes. '
                  'Preserve the full-body standing pose and all proportions. Only replace the green background '
                  'with a plain white background. No extra character, objects, text or changes to the outfit.')
        state.update(status='submission_unknown', prompt=prompt, generation_calls=1)
        atomic_json(state_path, state)
        task = generator.generate_view(prompt, record['url'])
        state.update(status='pending', gen_id=task)
        atomic_json(state_path, state)
        print(json.dumps({'status':'pending', 'model':DEFAULT_MODEL, 'gen_id':task}), flush=True)
        for attempt in range(90):
            result = generator.poll(task, model=DEFAULT_MODEL)
            data = result.get('data') or {}
            status = data.get('status', 'pending')
            state.update(status=status, checked_at=time.time(), poll_count=attempt+1)
            urls = []
            if status == 'completed':
                for output in data.get('outputs', []):
                    if output.get('item_status') == 'failed':
                        continue
                    item = output.get('data') or {}
                    if item.get('image_url'):
                        urls.append(item['image_url'])
                    urls.extend(item.get('image_urls') or [])
                if not urls:
                    state.update(status='failed', error='Task completed without a successful image.')
                else:
                    output_path = out / 'result.png'
                    output_path.write_bytes(generator.download_result(urls[0]))
                    state.update(output_path=str(output_path), image_urls=urls, image_downloaded=True,
                                 visual_acceptance='not_yet_reviewed')
            atomic_json(state_path, state)
            if state['status'] in ('completed', 'failed'):
                print(json.dumps({k:state.get(k) for k in ('status','gen_id','output_path','error')}), flush=True)
                return
            time.sleep(10)
        state['follow_up_needed'] = True
        atomic_json(state_path, state)
        print(json.dumps({'status':state['status'], 'gen_id':task, 'follow_up_needed':True}), flush=True)
    except Exception as error:
        state['error'] = str(error) if isinstance(error, UserError) else type(error).__name__
        state['checked_at'] = time.time()
        if not state.get('gen_id') and state['generation_calls'] == 0:
            state['status'] = 'publication_failed'
        atomic_json(state_path, state)
        print(json.dumps({'status':state['status'], 'error':state['error'],
                          'generation_calls':state['generation_calls']}), flush=True)
        raise SystemExit(1)
    finally:
        publisher.tunnel.close()
        generator.tunnel.close()
        state['temporary_tunnel_closed'] = True
        atomic_json(state_path, state)

if __name__ == '__main__':
    main()
