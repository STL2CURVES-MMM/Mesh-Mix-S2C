const { chromium } = require('playwright');
const fs=require('fs');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1024,height:1024}});
for(const n of process.argv.slice(2)){await p.setContent(`<html><body style="margin:0;background:transparent">${fs.readFileSync(n+'.svg','utf8')}</body></html>`);await p.screenshot({path:n+'.png',omitBackground:true});}
await b.close();})();
