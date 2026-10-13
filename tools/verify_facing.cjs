const fs=require('fs'),path=require('path');
const {chromium}=require('/Users/tu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const migration=JSON.parse(fs.readFileSync(path.join(root,'evidence/walk-fix.json')));
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 async function waitFrame(){for(let i=0;i<60;i++){const f=page.frames().find(f=>f.url().includes('viewer.html'));if(f&&await f.evaluate(()=>window.previewReady).catch(()=>false))return f;await page.waitForTimeout(150)}throw Error('Preview unavailable')}
 await page.goto('http://127.0.0.1:8780/?project='+migration.corrected_project);
 let frame=await waitFrame();await page.locator('#animation').selectOption('walk');
 await page.locator('#play').click();await page.waitForTimeout(100);
 const gait=await frame.evaluate(()=>{
   const samples=[];
   for(const p of players){const sk=p.skeleton,anim=sk.data.findAnimation('walk');const poses=[];let maxPitch=0,maxShin=-Infinity,minShin=Infinity;
    for(let i=0;i<=64;i++){sk.setToSetupPose();anim.apply(sk,-1,anim.duration*i/64,false,[],1,spine.MixBlend.replace,spine.MixDirection.mixIn);sk.updateWorldTransform(spine.Physics.update);
      for(const side of ['left','right']){const foot=sk.findBone(side+'-foot'),shin=sk.findBone(side+'-lower-leg');maxPitch=Math.max(maxPitch,Math.abs(Math.atan2(foot.c,foot.a)*180/Math.PI));maxShin=Math.max(maxShin,shin.rotation);minShin=Math.min(minShin,shin.rotation);if(foot.a<=0)throw Error('Toe points backward');}
      poses.push(sk.bones.flatMap(b=>[b.worldX,b.worldY,b.a,b.b,b.c,b.d]));}
    const loopError=Math.max(...poses[0].map((v,i)=>Math.abs(v-poses[64][i])));
    samples.push({poses,maxPitch,maxShin,minShin,loopError});
    sk.setToSetupPose();anim.apply(sk,-1,.675,false,[],1,spine.MixBlend.replace,spine.MixDirection.mixIn);sk.updateWorldTransform(spine.Physics.update);
   }
   return {matchingMotion:JSON.stringify(samples[0].poses)===JSON.stringify(samples[1].poses),maxSolePitch:samples[0].maxPitch,maxKneeFlexion:samples[0].maxShin,minKneeFlexion:samples[0].minShin,loopError:samples[0].loopError};
 });
 if(!gait.matchingMotion||gait.maxSolePitch>6.001||gait.maxKneeFlexion>0||gait.loopError>1e-4)throw Error('Gait regression: '+JSON.stringify(gait));
 await page.waitForTimeout(1200);await page.screenshot({path:path.join(root,'evidence/walk-right.png'),fullPage:true});
 await page.locator('#facing').selectOption('mirrored');await page.waitForTimeout(120);
 const mirrored=await frame.evaluate(()=>players.every(p=>getComputedStyle(p.canvas).transform==='matrix(-1, 0, 0, 1, 0, 0)' && p.paused));
 if(!mirrored)throw Error('Both paused previews must flip immediately');
 await page.screenshot({path:path.join(root,'evidence/walk-left.png'),fullPage:true});
 await page.locator('#play').click();await page.locator('#animation').selectOption('idle');await page.locator('#animation').selectOption('walk');
 const stayedMirrored=await frame.evaluate(()=>players.every(p=>p.canvas.style.transform==='scaleX(-1)'));
 await page.locator('#facing').selectOption('original');await page.waitForTimeout(120);
 const restored=await frame.evaluate(()=>players.every(p=>getComputedStyle(p.canvas).transform==='none'));
 // A new iframe, including the 3.8 player, must inherit the selected direction.
 await page.locator('#facing').selectOption('mirrored');
 await page.locator('#files').setInputFiles(path.join(root,'tests/fixtures/robot38.zip'));
 await page.waitForFunction(()=>appState().project?.runtime==='3.8');frame=await waitFrame();await page.waitForTimeout(150);
 const olderMirrored=await frame.evaluate(()=>players.every(p=>p.canvas.style.transform==='scaleX(-1)'));
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);const mobile=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
 const result={gait,mirrored,stayedMirrored,restored,olderMirrored,mobile,errors};fs.writeFileSync(path.join(root,'evidence/facing-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 await browser.close();if(!stayedMirrored||!restored||!olderMirrored||!mobile||errors.length)process.exit(1);
})().catch(e=>{console.error(e);process.exit(1)});
