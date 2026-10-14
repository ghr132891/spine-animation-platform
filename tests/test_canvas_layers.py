import tempfile,unittest,base64,copy
from pathlib import Path
from PIL import Image,ImageDraw
from app.canvas_layers import ownership,reconstruction,require
from app.core import UserError,png_bytes
from app.character import CharacterProject
class Tests(unittest.TestCase):
 def test_roundtrip_and_changed_offset(self):
  a=Image.new('RGBA',(20,20));ImageDraw.Draw(a).rectangle((1,1,18,18),fill=(80,40,20,255));r=Image.new('RGBA',(60,60));r.alpha_composite(a,(12,17));p={'name':'head','image':a,'box':[12,17,32,37]}
  report,im,_=reconstruction(r,[p],['head'],{'portrait':'head'});self.assertEqual(png_bytes(r),png_bytes(im));self.assertTrue(report['numerical_checks_passed'])
  review={'digest':report['digest'],'verdict':'ready','checks':dict.fromkeys(('ownership_correct','camera_consistent','identity_preserved','connections_complete'),True)};require(report,review)
  p['box']=[16,17,36,37];changed,_,_=reconstruction(r,[p],['head'],{'portrait':'head'})
  self.assertFalse(changed['numerical_checks_passed'])
  with self.assertRaises(UserError):require(changed,review)
 def test_ownership_collision(self):
  with self.assertRaises(UserError):ownership([{'name':'head','owns':['collar']},{'name':'torso','owns':['collar']}])
 def test_character_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   c=CharacterProject.create(Path(d),'test');im=Image.new('RGBA',(160,160));ImageDraw.Draw(im).rectangle((30,10,65,60),fill=(90,20,20,255));c.set_image(png_bytes(im),0)
   from app.character import segments
   # Include all expected names with distinct tiny sprites so fallback splitting never occurs.
   names=[n for n,_,_ in segments(c.meta['landmarks'])];layers=[]
   for i,n in enumerate(names):
    sprite=im if n=='head' else Image.new('RGBA',(1,1),(1,1,1,1));layers.append({'name':n,'image':base64.b64encode(png_bytes(sprite)).decode(),'offset':[0,0],'owns':[n],'complete':True})
   c.install_canvas_layers({'revision':c.meta['revision'],'layers':layers,'draw_order':names})
   self.assertEqual(c.base_parts()[0]['box'][:2],[0,0]);report,_,_=c.reconstruction();self.assertTrue(report['numerical_checks_passed'])
 def test_hidden_fill_preserves_visible_and_rejects_outside(self):
  from app.canvas_layers import partition
  ref=Image.new('RGBA',(20,20),(10,30,50,255))
  items=[{'name':'back','owns':['bag'],'polygon':[[0,0],[20,0],[20,20],[0,20]]},{'name':'front','owns':['coat'],'polygon':[[10,0],[20,0],[20,20],[10,20]]}]
  fill=Image.new('RGBA',(20,20));ImageDraw.Draw(fill).rectangle((10,0,19,19),fill=(80,60,40,255));items[0]['fill_image']=fill
  layers,owners=partition(ref,items,['back','front'])
  parts=[{'name':p['name'],'image':p['image'],'box':[*p['offset'],p['offset'][0]+p['image'].width,p['offset'][1]+p['image'].height]} for p in layers]
  report,result,_=reconstruction(ref,parts,['back','front'],owners)
  self.assertEqual(png_bytes(ref),png_bytes(result))
  fill.putpixel((0,0),(90,90,90,255))
  with self.assertRaises(UserError):partition(ref,items,['back','front'])
 def test_pixel_mask_overrides_polygon_without_losing_source(self):
  from app.canvas_layers import partition
  ref=Image.new('RGBA',(20,20),(30,80,20,255));mask=Image.new('L',(20,20));mask.putpixel((8,8),255)
  items=[{'name':'body','owns':['body'],'polygon':[[0,0],[20,0],[20,20],[0,20]]},{'name':'hand','owns':['hand'],'polygon':[[0,0],[20,0],[20,20],[0,20]],'mask':mask}]
  layers,_=partition(ref,items,['body','hand']);self.assertEqual(layers[1]['offset'],[8,8]);self.assertEqual(layers[1]['image'].size,(1,1))
 def test_explicit_background_seed_preserves_costume(self):
  from app.character import guide_cutout,checked_settings
  im=Image.new('RGBA',(160,160),'white');draw=ImageDraw.Draw(im);draw.rectangle((20,20,140,140),fill='black');draw.rectangle((40,40,60,60),fill='white');draw.rectangle((90,90,110,110),fill='white')
  result=guide_cutout(im,{}, {},checked_settings({'background_seeds':[[.3,.3]]}))
  self.assertEqual(result.getpixel((50,50))[3],0);self.assertEqual(result.getpixel((100,100))[3],255)
if __name__=='__main__':unittest.main()
