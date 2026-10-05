import fs from 'node:fs';
import vm from 'node:vm';
import { Worker } from 'node:worker_threads';
import assert from 'node:assert/strict';
const [oldFile, newFile] = process.argv.slice(2);
function fn(file) {
    const src=fs.readFileSync(file,'utf8');const start=src.indexOf('async function wait_for_memory_warmup() {');
    assert(start>=0);const end=src.indexOf('\n}\n',start);assert(end>start);return src.slice(start,end+2);
}
const old=fn(oldFile), fixed=fn(newFile);
const worker = new Worker(`
const {parentPort}=require('node:worker_threads');const vm=require('node:vm');
const context={memory_warmup:{key:'old',pending:true,promise:Promise.resolve()},memory_chat_key:()=> 'new'};
vm.runInNewContext(${JSON.stringify(old)}+';wait_for_memory_warmup().catch(()=>{});',context);
setTimeout(()=>parentPort.postMessage('timer'),10);
`,{eval:true,execArgv:[]});
const oldTimer = await new Promise(resolve=> {worker.on('message',()=>resolve(true));setTimeout(()=>resolve(false),200);});
await worker.terminate();assert.equal(oldTimer,false,'Old implementation must reproduce microtask starvation');
const chat=[];let key='current';let tests=0;
async function check(job,shouldReject=false) {
    const context=vm.createContext({memory_warmup:job,memory_chat_key:()=>key,getContext:()=>({chat}),memory_warmup_inputs_match:()=>true,Error});
    vm.runInContext(fixed,context);const result=context.wait_for_memory_warmup();
    if(shouldReject)await assert.rejects(result);else await result;tests++;
}
await check({key:'old',chat:[],pending:true,promise:Promise.resolve()},true);
await check({key:'current',chat,pending:false,promise:Promise.resolve()});
await check({key:'current',chat,pending:true,promise:Promise.resolve()},true);
await check({key:'current',chat,pending:false,cancelled:true,promise:Promise.resolve()},true);
await check({key:'current',chat,pending:true,error:new Error('tokenizer failed'),promise:Promise.resolve()},true);
const ready={key:'current',chat,pending:true};ready.promise=new Promise(resolve=>setTimeout(()=>{ready.pending=false;resolve()},10));await check(ready);
const switched={key:'current',chat,pending:true};switched.promise=new Promise(resolve=>setTimeout(()=>{key='different';resolve()},10));await check(switched,true);
assert.equal(tests,7);console.log(JSON.stringify({old_microtask_starvation_reproduced:true,fixed_cases_passed:tests,old_chat_generation_rejected:true}));
