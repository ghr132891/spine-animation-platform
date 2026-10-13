"""Offline checks of the generated walk template, not a natural-gait certificate."""
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from app.character import CharacterProject, make_skeleton
from app.rig import RigProject


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'evidence/reviewed-layout-v1.8/isolated/characters/fba502a6ace24498a8e2d44a9f3e372c'
NODE = shutil.which('node') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
SAMPLE = '''
const fs=require('fs'),path=require('path');
const {runtime,load,pose}=require(process.argv[1]);
const {audit}=require(process.argv[2]);
const spine=runtime('4.2'),rig=load(spine,'4.2',process.argv[3],'skeleton.json','skeleton.atlas');
const source=JSON.parse(fs.readFileSync(path.join(process.argv[3],'skeleton.json'),'utf8'));
const walk=rig.data.findAnimation('walk');
const gait=audit(spine,'4.2',rig,'walk',{anchor:'hip',axis:'x',pairs:[
  {arm:'l-hand',leg:'l-foot'}, {arm:'r-hand',leg:'r-foot'}]});
const frames=Array.from({length:385},(_,i)=>{
  const frame=pose(spine,'4.2',rig,'walk',walk.duration*i/384);
  return {time:walk.duration*i/384,bones:frame.bones};
});
// Remove curves only in an isolated export, then compare actual FK foot speed
// at all authored keys. Loading JSON alone would not detect ignored controls.
const linear=JSON.parse(JSON.stringify(source));
for(const bone of Object.values(linear.animations.walk.bones))
 for(const timeline of Object.values(bone))for(const frame of timeline)delete frame.curve;
fs.writeFileSync(path.join(process.argv[3],'linear-test.json'),JSON.stringify(linear));
const linearRig=load(spine,'4.2',process.argv[3],'linear-test.json','skeleton.atlas');
const times=Array.from(new Set(Object.values(source.animations.walk.bones)
 .flatMap(b=>Object.values(b)).flatMap(t=>t.map(f=>f.time??0)))).filter(t=>t>0&&t<walk.duration-1e-6);
const h=walk.duration/8192;
const jump=(skeleton,name,time)=>{
 const points=[time-h,time,time+h].map(t=>pose(spine,'4.2',skeleton,'walk',t).bones.find(b=>b.name===name));
 return Math.hypot(...['x','y'].map(axis=>(points[2][axis]-2*points[1][axis]+points[0][axis])/h));
};
const velocity=Object.fromEntries(['l','r'].map(side=>[side,{
 curveMaximum:Math.max(...times.map(t=>jump(rig,side+'-foot',t))),
 linearMaximum:Math.max(...times.map(t=>jump(linearRig,side+'-foot',t))),
 legLength:source.bones.find(b=>b.name===side+'-upper-leg').length+source.bones.find(b=>b.name===side+'-lower-leg').length
}]));
console.log(JSON.stringify({gait,frames,velocity,metadata:source.skeleton.gait}));
'''


def snapshot(folder):
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()}


@unittest.skipUnless(FIXTURE.exists() and Path(NODE).exists(), 'Recorded real source and official runtime are required')
class GeneratedGaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = snapshot(FIXTURE)
        cls.tmp = tempfile.TemporaryDirectory(prefix='spine-generated-gait-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.results = {}
        for facing in ('right', 'left'):
            storage = Path(cls.tmp.name) / facing
            destination = storage / 'characters' / FIXTURE.name
            shutil.copytree(FIXTURE, destination)
            c = CharacterProject(destination)
            settings = {**c.meta['settings'], 'facing': facing}
            result = c.build(storage, {'revision': c.meta['revision'], 'nonce': uuid.uuid4().hex,
                                      'landmarks': copy.deepcopy(c.meta['landmarks']),
                                      'masks': copy.deepcopy(c.meta['masks']), 'settings': settings})
            saved = result['versions'][-1]
            rig = RigProject(storage / 'rigs' / saved['rig_id'])
            export = storage / 'export'
            export.mkdir()
            files, _ = rig.files()
            for name, data in files.items():
                path = export / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            process = subprocess.run([NODE, '-e', SAMPLE, str(ROOT / 'tools/verify_rig_runtime.cjs'),
                                      str(ROOT / 'tools/gait_audit.cjs'), str(export)],
                                     text=True, capture_output=True, check=True, timeout=45)
            cls.results[facing] = json.loads(process.stdout)

    def test_both_facing_directions_have_opposed_same_side_hands_and_feet(self):
        for facing, result in self.results.items():
            for pair in result['gait']['pairs']:
                with self.subTest(facing=facing, arm=pair['arm'], foot=pair['leg']):
                    self.assertGreater(pair['armRange'], 1)
                    self.assertGreater(pair['legRange'], 1)
                    self.assertLess(pair['correlation'], -.8)
                    self.assertEqual(pair['assessment'], 'opposed')

    def test_stance_ankle_origins_are_level_in_actual_world_coordinates(self):
        # The new template has 60% support with short double-support windows.
        # This only checks ankle consistency while foot pitch is constant;
        # the separate pixel audit checks the actual visible shoe sole.
        for facing, result in self.results.items():
            duration = result['gait']['duration']
            for side, phase in (('l', 0), ('r', .5)):
                support_frames=sorted([frame for frame in result['frames'][:-1]
                                       if (frame['time']/duration+phase)%1<.6],
                                      key=lambda frame:(frame['time']/duration+phase)%1)
                support = [next(bone for bone in frame['bones'] if bone['name'] == side + '-foot')
                           for frame in support_frames]
                heights = [bone['y'] for bone in support]
                with self.subTest(facing=facing, side=side):
                    self.assertGreaterEqual(len(support), 57)
                    self.assertLess(max(heights) - min(heights), .12)
                    direction = -1 if facing == 'right' else 1
                    self.assertGreater(direction * (support[-1]['x'] - support[0]['x']), 10)

    def test_loop_closes_for_rendered_vertices_and_actual_bone_origins(self):
        for facing, result in self.results.items():
            with self.subTest(facing=facing):
                self.assertLess(result['gait']['loopMaxVertexError'], 1e-3)
                start, end = result['frames'][0]['bones'], result['frames'][-1]['bones']
                self.assertEqual([bone['name'] for bone in start], [bone['name'] for bone in end])
                error = max(abs(a[key] - b[key]) for a, b in zip(start, end) for key in ('x', 'y', 'ex', 'ey'))
                self.assertLess(error, 1e-3)

    def test_both_planted_feet_cancel_the_same_scene_velocity(self):
        for facing,result in self.results.items():
            duration=result['gait']['duration'];speeds=[]
            scene_speed=result['metadata']['scene_speed']
            self.assertEqual(result['metadata']['facing'],facing)
            self.assertGreater(scene_speed*(1 if facing=='right' else -1),0)
            for side,phase in (('l',0),('r',.5)):
                samples=sorted([(f['time']/duration+phase)%1,
                                next(b['x'] for b in f['bones'] if b['name']==side+'-foot')]
                               for f in result['frames'][:-1]
                               if .08<(f['time']/duration+phase)%1<.52)
                speeds.append((samples[-1][1]-samples[0][1])/((samples[-1][0]-samples[0][0])*duration))
                with self.subTest(facing=facing,side=side):
                    self.assertLess(abs(speeds[-1]+scene_speed),.002)
                    self.assertEqual(result['metadata']['feet'][side]['slot'],side+'-upper-leg')
            with self.subTest(facing=facing):
                self.assertLess(abs(speeds[0]-speeds[1]),.002)

    def test_runtime_curves_reduce_keyframe_speed_changes_without_static_motion(self):
        for facing, result in self.results.items():
            self.assertEqual(result['metadata']['motion_curve'], 'bezier-hermite-v1')
            self.assertEqual(result['metadata']['stance'], .6)
            self.assertEqual(result['metadata']['unreachable_samples'], 0)
            for side, speed in result['velocity'].items():
                with self.subTest(facing=facing, side=side):
                    self.assertLess(speed['curveMaximum'], speed['linearMaximum']*.6)
                    self.assertLess(speed['curveMaximum']/speed['legLength'], .03)

    def test_contact_metadata_names_actual_standalone_shoe_attachments(self):
        source=CharacterProject(FIXTURE)
        image=source.cutout();parts=[]
        for side in ('l','r'):
            ankle=source.meta['landmarks'][side+'-ankle']
            x,y=round(ankle[0]*image.width),round(ankle[1]*image.height)
            sprite=Image.new('RGBA',(60,32),(0,0,0,0))
            ImageDraw.Draw(sprite).rectangle((2,2,57,29),fill='#78534a')
            parts.append({'name':side+'-foot','image':sprite,'box':(x-20,y,x+40,y+32),
                          'complete':True,'pixel_count':56*28})
        skeleton=make_skeleton(image,source.meta['landmarks'],parts,source.meta['settings'],
                               source.meta['masks'],['l-foot','r-foot'])
        self.assertEqual({slot['name'] for slot in skeleton['slots']},{'l-foot','r-foot'})
        for side in ('l','r'):
            profile=skeleton['skeleton']['gait']['feet'][side]
            self.assertEqual(profile['source'],'opaque-lower-envelope')
            self.assertEqual(profile['slot'],side+'-foot')

    def test_incomplete_shoes_do_not_claim_a_rigid_contact_attachment(self):
        source=CharacterProject(FIXTURE);image=source.cutout();parts=[]
        for side in ('l','r'):
            x,y=[round(v*dimension) for v,dimension in zip(source.meta['landmarks'][side+'-ankle'],image.size)]
            parts.append({'name':side+'-foot','image':Image.new('RGBA',(60,32),'#78534a'),
                          'box':(x-20,y,x+40,y+32),'complete':False,'pixel_count':60*32})
        skeleton=make_skeleton(image,source.meta['landmarks'],parts,source.meta['settings'],
                               source.meta['masks'],['l-foot','r-foot'])
        self.assertIsNone(skeleton['skeleton']['gait']['ground_y'])
        for side in ('l','r'):
            profile=skeleton['skeleton']['gait']['feet'][side]
            self.assertEqual(profile['source'],'ankle-fallback')
            self.assertTrue(profile['requires_visual_review'])
            self.assertNotIn('slot',profile)

    def test_original_recorded_character_and_pixels_are_unchanged(self):
        self.assertEqual(snapshot(FIXTURE), self.original)


if __name__ == '__main__':
    unittest.main()
