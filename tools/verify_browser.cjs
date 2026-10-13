const fs=require('fs'),path=require('path');
const {chromium}=require('/Users/tu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const context=await browser.newContext({viewport:{width:1500,height:1000},acceptDownloads:true});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8780/');await page.waitForTimeout(500);await page.screenshot({path:path.join(root,'evidence/welcome.png'),fullPage:true});
 await page.locator('#load-demo').click();
 await page.waitForFunction(()=>window.appState()?.project);
 async function frame(){let f;for(let n=0;n<30;n++){f=page.frames().find(f=>f.url().includes('viewer.html'));if(f&&await f.evaluate(()=>window.previewReady).catch(()=>false))return f;await page.waitForTimeout(200)}throw Error('Viewer failed: '+await page.locator('#notice').textContent())}
 let f=await frame();await page.waitForTimeout(1300);
 const beforeId=await page.evaluate(()=>appState().project.id);
 await page.locator('.part-row[data-id="r1"]').click();
 await page.locator('#hue').evaluate(e=>{e.value='125';e.dispatchEvent(new Event('input'))});
 await page.locator('#apply-color').click();
 await page.waitForFunction(()=>appState().project.revision===1);
 f=await frame();await page.waitForTimeout(1200);
 await page.locator('#animation').selectOption('walk');
 await page.waitForTimeout(500);
 const motions=await f.evaluate(()=>{
  const results=[];for(const p of players){p.pause();const sk=p.skeleton,anim=sk.data.findAnimation('walk');const frames=[];for(let i=0;i<=30;i++){sk.setToSetupPose();anim.apply(sk,-1,anim.duration*i/30,false,[],1,spine.MixBlend.replace,spine.MixDirection.mixIn);sk.updateWorldTransform(spine.Physics.update);frames.push(sk.bones.flatMap(b=>[b.worldX,b.worldY,b.a,b.b,b.c,b.d]));}results.push(frames)}return {same:JSON.stringify(results[0])===JSON.stringify(results[1]),finite:results.flat(2).every(Number.isFinite),motion:Math.max(...results[0][0].map((v,i)=>Math.abs(v-results[0][8][i])))};
 });
 if(!motions.same||!motions.finite||motions.motion===0)throw Error('Motion invariant failed');
 await page.screenshot({path:path.join(root,'evidence/editor.png'),fullPage:true});
 await page.locator('#validate').click();await page.waitForFunction(()=>document.querySelector('#validation-dialog').open);await page.screenshot({path:path.join(root,'evidence/validation.png'),fullPage:true});await page.locator('#close-validation').click();
 const downloadEvent=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadEvent;await download.saveAs(path.join(root,'evidence/browser-export.zip'));
 await page.locator('#undo').click();await page.waitForFunction(()=>appState().project.revision===2);const undoCleared=await page.evaluate(()=>appState().project.changed.length===0);
 await page.reload();await page.waitForFunction(()=>appState().project?.revision===2);const persistence=await page.evaluate(id=>appState().project.id===id,beforeId);await frame();
 // Import a real ZIP through the normal file picker, exercising the older player.
 await page.locator('#files').setInputFiles(path.join(root,'tests/fixtures/robot38.zip'));
 await page.waitForFunction(()=>appState().project?.runtime==='3.8');f=await frame();await page.waitForTimeout(1200);await page.locator('#animation').selectOption('walk');await page.waitForTimeout(400);
 const runtime38=await f.evaluate(()=>({version:players[0].skeleton.data.version,animation:players[0].animationState.getCurrent(0)?.animation.name,bones:players[0].skeleton.bones.length}));
 await page.screenshot({path:path.join(root,'evidence/spine38.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(400);await page.screenshot({path:path.join(root,'evidence/mobile.png'),fullPage:true});const noOverflow=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
 await page.goto('http://127.0.0.1:8780/settings.html');const fieldType=await page.locator('#key').getAttribute('type'),fieldValue=await page.locator('#key').inputValue();
 const result={motions,undoCleared,persistence,runtime38,noOverflow,credentialInputMasked:fieldType==='password',credentialNotReturned:fieldValue==='',errors};fs.writeFileSync(path.join(root,'evidence/browser-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 await browser.close();if(errors.length||!undoCleared||!persistence||!noOverflow)process.exit(1);
})().catch(e=>{console.error(e);process.exit(1)});
