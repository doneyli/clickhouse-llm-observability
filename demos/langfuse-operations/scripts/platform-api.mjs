#!/usr/bin/env node
/** Narrow adapter for surfaces absent from the Public API CLI. Secrets use Node's env loader. */
import {readFileSync} from 'node:fs';
const input = JSON.parse(readFileSync(0, 'utf8'));
const base = new URL(input.host);
if (!['localhost','127.0.0.1'].includes(base.hostname) || base.protocol !== 'http:' || base.username || base.password) throw new Error('Only a local HTTP Langfuse instance is supported.');
const jar = new Map();
async function request(path, options={}) {
  const headers = {...options.headers};
  if (jar.size) headers.Cookie = [...jar].map(([k,v])=>`${k}=${v}`).join('; ');
  const response = await fetch(new URL(path,base), {...options, headers, redirect:'manual', signal:AbortSignal.timeout(20000)});
  for(const cookie of response.headers.getSetCookie()) {const pair=cookie.split(';')[0],i=pair.indexOf('=');jar.set(pair.slice(0,i),pair.slice(i+1));}
  const text = await response.text();let body;try{body=JSON.parse(text);}catch{body={message:text.slice(0,300)};}
  return {status:response.status,body};
}
const json = () => ({'Content-Type':'application/json'});
try {
  let result;
  if (input.mode === 'instance') {
    if(!process.env.ADMIN_API_KEY) throw new Error('ADMIN_API_KEY is not configured.');
    if(!/^\/api\/admin\/organizations(?:\/[A-Za-z0-9_-]+(?:\/apiKeys(?:\/[A-Za-z0-9_-]+)?)?)?$/.test(input.path) || !['GET','POST','DELETE'].includes(input.method)) throw new Error('Instance operation is not allowlisted.');
    result=await request(input.path,{method:input.method,headers:{...json(),Authorization:`Bearer ${process.env.ADMIN_API_KEY}`},...(input.body?{body:JSON.stringify(input.body)}:{})});
  } else {
    const health=await request('/api/public/health');
    if(health.body.version !== '4.76.0') throw new Error('Native UI adapter requires Langfuse 4.76.0. Use the native UI or validate a new contract before upgrading.');
    if(!process.env.DEMO_EMAIL || !process.env.DEMO_PASSWORD) throw new Error('Demo session credentials are missing.');
    if(input.mode === 'signup') {
      result=await request('/api/auth/signup',{method:'POST',headers:json(),body:JSON.stringify({name:input.name,email:process.env.DEMO_EMAIL,password:process.env.DEMO_PASSWORD})});
    } else if(input.mode === 'rpc') {
      const allowed={'prompts.getProtectedLabels':'GET','prompts.addProtectedLabel':'POST','prompts.setLabels':'POST','auditLogs.all':'GET'};
      if(allowed[input.procedure]!==input.method) throw new Error('Native procedure is not allowlisted.');
      const csrf=await request('/api/auth/csrf');
      if(!csrf.body.csrfToken) throw new Error('Native session CSRF initialization failed.');
      const login=await request('/api/auth/callback/credentials',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({csrfToken:csrf.body.csrfToken,email:process.env.DEMO_EMAIL,password:process.env.DEMO_PASSWORD,callbackUrl:base.href,json:'true'}).toString()});
      if(login.status>=400 || ![...jar.keys()].some(k=>k.includes('session-token'))) throw new Error('Native demo user login failed. Check password sign-in and email policy.');
      const wire={json:input.body};
      result=await request('/api/trpc/'+input.procedure+(input.method==='GET'?'?input='+encodeURIComponent(JSON.stringify(wire)):''),{method:input.method,headers:json(),...(input.method==='POST'?{body:JSON.stringify(wire)}:{})});
      if(result.body.result) result.body=result.body.result.data.json;
    } else throw new Error('Unknown adapter mode.');
  }
  process.stdout.write(JSON.stringify(result));
} catch(error) {process.stdout.write(JSON.stringify({status:0,body:{message:error.message}}));process.exitCode=1;}
