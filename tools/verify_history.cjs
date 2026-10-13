const fs=require('fs'),path=require('path'),crypto=require('crypto');
const {chromium}=require('/Users/tu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:8780';
const digest=buffer=>crypto.createHash('sha256').update(buffer).digest('hex');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true,args:['--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 const context=await browser.newContext({viewport:{width:1500,height:1000}});let page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/?project=b0ec016ff71e4914ad4eb58264a9bdd1');await page.waitForFunction(()=>appState().project);
 // Import from an old project's URL: reloading must keep the newly imported one.
 await page.locator('#files').setInputFiles({name:'history-qa.zip',mimeType:'application/zip',buffer:fs.readFileSync(path.join(root,'tests/fixtures/robot38.zip'))});
 await page.waitForFunction(()=>appState().project?.name==='history-qa');const id=await page.evaluate(()=>appState().project.id);
 const urlUpdated=new URL(page.url()).searchParams.get('project')===id;
 await page.reload();await page.waitForFunction(id=>appState().project?.id===id,id);
 const api='/api/projects/'+id;
 const hash=async()=>digest(await(await page.request.get(base+api+'/page?index=0')).body());
 await page.locator('.part-row[data-id="r1"]').click();
 await page.locator('#hue').evaluate(e=>{e.value='90';e.dispatchEvent(new Event('input'))});await page.locator('#apply-color').click();await page.waitForFunction(()=>appState().project.revision===1);const first=await hash();
 await page.locator('#save-version').click();await page.locator('#version-name').fill('保留的蓝色方案');await page.locator('#save-version-form button[type="submit"]').click();await page.waitForFunction(()=>appState().project.history_count===3);
 await page.locator('#hue').evaluate(e=>{e.value='-110';e.dispatchEvent(new Event('input'))});await page.locator('#apply-color').click();await page.waitForFunction(()=>appState().project.revision===2);const second=await hash();if(first===second)throw Error('fixture colors did not change');
 const versions=await(await page.request.get(base+api+'/history')).json(),saved=versions.find(v=>v.label==='保留的蓝色方案');
 await page.locator('#history').click();await page.locator(`[data-snapshot="${saved.id}"]`).waitFor();await page.screenshot({path:path.join(root,'evidence/history-versions.png'),fullPage:true});
 await page.locator(`[data-snapshot="${saved.id}"] button`).click();await page.waitForFunction(()=>appState().project.revision===3);const restored=(await hash())===first;
 const futureKept=(await(await page.request.get(base+api+'/history')).json()).some(v=>v.revision===2);
 // Twelve saved fake candidates, explicitly isolated from real generation/billing.
 const jobsDir=path.join(root,'storage/projects',id,'jobs');fs.mkdirSync(jobsDir,{recursive:true});
 for(let i=0;i<12;i++){const jobId='c'+i.toString(16).padStart(31,'0'),j={id:jobId,region:'r1',revision:0,status:'completed',candidate_ready:true,created_at:1791450000+i,prompt:'历史功能测试候选 '+i,mode:'material',layout:{mode:'material',target_width:108,target_height:125}};fs.writeFileSync(path.join(jobsDir,jobId+'.json'),JSON.stringify(j));fs.copyFileSync(path.join(root,'demo/robot.png'),path.join(jobsDir,jobId+'.png'));}
 await page.close();page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/?project='+id);await page.waitForFunction(id=>appState().project?.id===id,id);const reopened=(await hash())===first;
 await page.locator('#history').click();await page.locator('[data-history="jobs"]').click();await page.waitForFunction(()=>document.querySelectorAll('#history-list [data-job]').length===12);
 const allCandidates=await page.locator('#history-list [data-job]').count();await page.locator('#history-list [data-job]').last().getByRole('button',{name:'查看候选图'}).click();await page.locator('#ai-result img').waitFor();
 const oldCandidateVisible=(await page.locator('#ai-result img').getAttribute('src')).includes('c'+'0'.repeat(31));
 await page.locator('#history').click();await page.locator('[data-history="projects"]').click();await page.locator(`[data-project="${id}"]`).waitFor();
 await page.setViewportSize({width:390,height:844});await page.waitForTimeout(150);const mobile=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);await page.screenshot({path:path.join(root,'evidence/history-mobile.png'),fullPage:true});
 const report={urlUpdated,restored,futureKept,reopened,allCandidates,oldCandidateVisible,mobile,errors};fs.writeFileSync(path.join(root,'evidence/history-browser-validation.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 await browser.close();
 // Keep the test project recoverable, outside the user's project list.
 const archive=path.join(root,'evidence/history-qa-projects');fs.mkdirSync(archive,{recursive:true});fs.renameSync(path.join(root,'storage/projects',id),path.join(archive,id));
 if(!urlUpdated||!restored||!futureKept||!reopened||allCandidates!==12||!oldCandidateVisible||!mobile||errors.length)process.exit(1);
})().catch(e=>{console.error(e);process.exit(1)});
