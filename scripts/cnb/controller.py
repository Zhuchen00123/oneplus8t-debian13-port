"""Phone-side address discovery and CNB runtime provisioning; no payload storage."""
import os,pathlib,json,urllib.request,subprocess,shlex,re,time,signal
from publish_endpoints import publish
BASE=pathlib.Path('/srv/kebab-strelay/private');private=json.loads((BASE/'private.json').read_text())
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}));REPO='zc354/kebab-sillytavern-relay';alive=True
def stop(*_):
 global alive;alive=False
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
def api(path):
 req=urllib.request.Request('https://api.cnb.cool'+path,headers={'Authorization':'Bearer '+private['cnb_token']})
 with OPENER.open(req,timeout=15) as r:return json.load(r)
def atomic(path,data,mode=0o600):
 t=path.with_suffix(path.suffix+'.new');t.write_text(data);os.chmod(t,mode);t.replace(path)
def endpoint_prefix(dest,port):
 ssh=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=kebab-cnb-relay','-o','UserKnownHostsFile='+str(BASE/'known_hosts'),'-o','ConnectTimeout=15','-p',str(port),dest,'cat /tmp/kebab-strelay-endpoint.json']
 p=subprocess.run(ssh,capture_output=True,text=True,timeout=25);assert p.returncode==0
 host=json.loads(p.stdout)['host']
 if not re.fullmatch(r'[a-zA-Z0-9-]+-9003\.cnb\.run',host):raise ValueError('Unexpected dedicated endpoint format')
 return host[:-len('-9003.cnb.run')]
def workspaces():
 rows=[];page=1
 while True:
  data=api('/workspace/list?page='+str(page)+'&pageSize=100')
  rows.extend(data.get('list',[]))
  if not data.get('hasMore'):return rows
  if not data.get('list'):raise RuntimeError('Invalid workspace pagination')
  page+=1
  if page>100:raise RuntimeError('Workspace pagination limit reached')

def cycle():
 rows=workspaces();rows=[r for r in rows if r.get('status')=='running' and r.get('slug')==REPO and r.get('branch')=='main']
 if not rows:
  countfile=BASE/'last-start';now=time.time()
  if not countfile.exists() or now-float(countfile.read_text())>600:
   atomic(countfile,str(now));req=urllib.request.Request('https://api.cnb.cool/'+REPO+'/-/workspace/start',data=b'{"branch":"main"}',headers={'Authorization':'Bearer '+private['cnb_token'],'Content-Type':'application/json'},method='POST')
   with OPENER.open(req,timeout=30) as r:r.read()
  raise ValueError('Waiting for dedicated workspace startup')
 if len(rows)!=1:raise ValueError('Expected one dedicated relay workspace')
 detail=api('/'+REPO+'/-/workspace/detail/'+rows[0]['sn']);parts=shlex.split(detail['ssh']);port=22;dest=None
 if not parts or parts[0]!='ssh':raise ValueError('Unexpected SSH metadata')
 for i,a in enumerate(parts[1:],1):
  if a=='-p':port=int(parts[i+1])
  elif '@' in a and not a.startswith('-'):dest=a
 if not dest or not re.fullmatch(r'[A-Za-z0-9_.@-]+',dest):raise ValueError('Unexpected SSH destination')
 ssh=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=kebab-cnb-relay','-o','UserKnownHostsFile='+str(BASE/'known_hosts'),'-o','ConnectTimeout=15','-o','ServerAliveInterval=15','-p',str(port),dest,'python3 /workspace/sillytavern-relay/bootstrap.py']
 creds={k:private[k] for k in ['ssh_secret','ssh_token','http_secret','http_token']}
 p=subprocess.run(ssh,input=json.dumps(creds),capture_output=True,text=True,timeout=45)
 if p.returncode!=0:raise RuntimeError('CNB provisioning failed')
 result=json.loads(p.stdout);assert result['ready']
 shared=json.loads((BASE/'shared-private.json').read_text())
 ssh[-1]='python3 /workspace/shared-relay/bootstrap.py'
 extra=subprocess.run(ssh,input=json.dumps(shared),capture_output=True,text=True,timeout=120)
 if extra.returncode or not json.loads(extra.stdout)['ready']:raise RuntimeError('Shared relay provisioning failed')
 prefix=endpoint_prefix(dest,port);changed=False
 for name,relayport,target in [('ssh',9001,'127.0.0.1:22'),('http',9002,'127.0.0.1:8000')]:
  f=BASE/(name+'.env');text='MCB_HOST=%s-%s.cnb.run\nMCB_PORT=443\nMCB_SECRET=%s\nMCB_SESSION=kebab-st-%s\nMCB_MC=%s\n'%(prefix,relayport,private[name+'_secret'],name,target)
  if not f.exists() or f.read_text()!=text:
   atomic(f,text);changed=True;subprocess.run(['systemctl','restart','kebab-strelay-'+name+'.service'],check=True,capture_output=True)
  elif subprocess.run(['systemctl','is-active','--quiet','kebab-strelay-'+name+'.service']).returncode:
   subprocess.run(['systemctl','start','kebab-strelay-'+name+'.service'],check=True,capture_output=True)
 mcenv=BASE/'mc.env';current=mcenv.read_text();values=current.splitlines();host=prefix+'-9010.cnb.run'
 updated='\n'.join('MCB_HOST='+host if line.startswith('MCB_HOST=') else 'MCB_PORT=443' if line.startswith('MCB_PORT=') else line for line in values)+'\n'
 if updated!=current:
  atomic(mcenv,updated);changed=True;subprocess.run(['systemctl','restart','kebab-mcbridge-client.service'],check=True,capture_output=True)
 elif subprocess.run(['systemctl','is-active','--quiet','kebab-mcbridge-client.service']).returncode:
  subprocess.run(['systemctl','start','kebab-mcbridge-client.service'],check=True,capture_output=True)
 status={'ready':True,'mc_wss_host':host,'mcp_wss_host':prefix+'-9000.cnb.run','shared_services':True,'checked_at':int(time.time()),'https_url':'https://'+prefix+'-9003.cnb.run/','ssh_wss_host':prefix+'-9001.cnb.run','workspace_sn':rows[0]['sn'],'address_changed':changed}
 atomic(BASE/'entry-ssh.json',json.dumps({'workspace_sn':rows[0]['sn'],'https_url':status['https_url'],'dest':dest,'port':port,'config_digest':__import__('hashlib').sha256(json.dumps(creds,sort_keys=True).encode()).hexdigest()}),0o600)
 atomic(BASE/'status.json',json.dumps(status),0o644)
 # Relay provisioning and address verification have succeeded. Legacy Git
 # publication is independent and must not age out this verified live endpoint.
 publish(prefix,shared)
 return changed
last=None
while alive:
 try:
  changed=cycle();state='connected'
  if changed:print('CNB address synchronized; independent clients restarted',flush=True)
 except Exception as e:
  state='waiting:'+type(e).__name__
  # Do not print exception messages: they can include connection metadata or URLs.
 if state!=last:print(state,flush=True);last=state
 for _ in range(60):
  if not alive:break
  time.sleep(1)
