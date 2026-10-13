const fs=require('fs'),path=require('path');
const {chromium}=require('/Users/tu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const record=JSON.parse(fs.readFileSync(path.join(root,'evidence/material-test.json')));
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const page=await browser.newPage({viewport:{width:1500,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8780/?project='+record.project);await page.waitForFunction(()=>appState().project?.id);
 await page.locator('.part-row[data-id="r1"]').click();await page.locator('[data-edit="ai"]').click();
 await page.locator('#ai-result img').waitFor();await page.waitForFunction(()=>document.querySelector('#ai-result img').complete);
 let frame;for(let i=0;i<30;i++){frame=page.frames().find(f=>f.url().includes('viewer.html'));if(frame&&await frame.evaluate(()=>window.previewReady))break;await page.waitForTimeout(200)}
 await page.waitForTimeout(1300);await page.screenshot({path:path.join(root,'evidence/ai-applied.png'),fullPage:true});
 const checks=await frame.evaluate(()=>{const snapshots=[];for(const p of players){p.pause();const s=p.skeleton,a=s.data.findAnimation('walk');let frames=[];for(let i=0;i<=30;i++){s.setToSetupPose();a.apply(s,-1,a.duration*i/30,false,[],1,spine.MixBlend.replace,spine.MixDirection.mixIn);s.updateWorldTransform(spine.Physics.update);frames.push(s.bones.flatMap(b=>[b.worldX,b.worldY,b.a,b.b,b.c,b.d]))}snapshots.push(frames)}return {same_motion:JSON.stringify(snapshots[0])===JSON.stringify(snapshots[1]),finite:snapshots.flat(2).every(Number.isFinite)}});
 const response=await page.request.get('http://127.0.0.1:8780/api/projects/'+record.project+'/export');fs.writeFileSync(path.join(root,'evidence/ai-reskin.zip'),await response.body());
 const status=await (await page.request.get('http://127.0.0.1:8780/api/provider')).json();
 console.log(JSON.stringify({...checks,restored_candidate_visible:await page.locator('#ai-result img').isVisible(),credential_connected:status.state==='connected',errors},null,2));
 if(!checks.same_motion||!checks.finite||errors.length)throw Error('AI verification failed');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
