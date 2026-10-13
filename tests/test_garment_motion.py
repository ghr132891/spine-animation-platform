import copy,math,unittest
from app.garment_motion import add_long_hem_motion

def sample(depth=140):
 bones=[{'name':'hip'},{'name':'l-upper-leg','length':80},{'name':'l-lower-leg','length':80},{'name':'r-upper-leg','length':80},{'name':'r-lower-leg','length':80}]
 rows=[(-30,-depth),(-1,-depth),(1,-depth),(30,-depth),(0,25)]
 vertices=[]
 for x,y in rows:vertices.extend([1,0,x,y,1])
 return {'skeleton':{},'bones':bones,'skins':[{'attachments':{'torso':{'torso':{'type':'mesh','uvs':[0,0]*5,'vertices':vertices}}}}],'animations':{c:{'bones':{'l-upper-leg':{'rotate':[{'time':0,'value':20},{'time':1.2,'value':20}]}}} for c in ['walk','idle']}}

def rows(s):
 v=s['skins'][0]['attachments']['torso']['torso']['vertices'];out=[];p=0
 while p<len(v):
  n=int(v[p]);p+=1;out.append([v[p+4*i:p+4*i+4] for i in range(n)]);p+=4*n
 return out

class GarmentMotionTests(unittest.TestCase):
 def test_short_costume_is_untouched(self):
  s=sample(50);before=copy.deepcopy(s);self.assertFalse(add_long_hem_motion(s,1.2));self.assertEqual(s,before)
 def test_setup_vertices_and_weight_sums_are_preserved(self):
  s=sample();original=rows(s);self.assertTrue(add_long_hem_motion(s,1.2))
  for old,new in zip(original,rows(s)):
   self.assertAlmostEqual(sum(w for b,x,y,w in new),1)
   for axis in [1,2]:self.assertAlmostEqual(sum(row[axis]*row[3] for row in new),old[0][axis])
 def test_centre_weights_are_continuous_not_binary_split(self):
  s=sample();add_long_hem_motion(s,1.2);r=rows(s)
  w=lambda row:sum(v[3] for v in row if v[0]==5)
  self.assertLess(abs(w(r[1])-w(r[2])),.06)
 def test_neutral_small_swing_preserves_leg_animation_and_closes_cycle(self):
  s=sample();leg=copy.deepcopy(s['animations']['walk']['bones']['l-upper-leg']);add_long_hem_motion(s,1.2)
  self.assertEqual(leg,s['animations']['walk']['bones']['l-upper-leg'])
  for name in ('hem-front','hem-back'):
   frames=s['animations']['walk']['bones'][name]['rotate'];values=[f.get('value',0) for f in frames]
   self.assertLessEqual(max(map(abs,values)),3.4);self.assertLess(min(values),0);self.assertAlmostEqual(values[0],values[-1])
 def test_direction_and_duplicate_application(self):
  a=sample();b=sample();add_long_hem_motion(a,1.2,1);add_long_hem_motion(b,1.2,-1)
  av=a['animations']['walk']['bones']['hem-front']['rotate'];bv=b['animations']['walk']['bones']['hem-front']['rotate']
  for x,y in zip(av,bv):self.assertAlmostEqual(x['value'],-y['value'])
  before=copy.deepcopy(a);self.assertFalse(add_long_hem_motion(a,1.2));self.assertEqual(a,before)
 def test_invalid_period_does_not_mutate(self):
  s=sample();before=copy.deepcopy(s)
  with self.assertRaises(ValueError):add_long_hem_motion(s,float('nan'))
  self.assertEqual(s,before)
if __name__=='__main__':unittest.main()
