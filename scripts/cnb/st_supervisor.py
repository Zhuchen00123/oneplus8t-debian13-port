"""CNB byte forwarding only. No application data or provider credentials."""
import pathlib,json,subprocess,os,time,signal,hashlib,fcntl
HERE=pathlib.Path(__file__).resolve().parent
STATE=pathlib.Path.home()/'.kebab-strelay';STATE.mkdir(mode=0o700,exist_ok=True);os.chmod(STATE,0o700)
lock=(STATE/'runtime.lock').open('w')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit(0)
(STATE/'supervisor.pid').write_text(str(os.getpid()));os.chmod(STATE/'supervisor.pid',0o600)
alive=True;children={};fingerprint=None
def stop(*_):
 global alive;alive=False
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
BIN=HERE.parent/'bin/mcbridge-linux-amd64'
assert hashlib.sha256(BIN.read_bytes()).hexdigest()=='72e099e6aa0195ee9795bd1b60159f30aefbfe18687211a927b4dedd1d4b8873'
os.chmod(BIN,0o755)
proxy=os.environ.get('CNB_VSCODE_PROXY_URI','')
if proxy:
 import re
 host=re.sub(r'^https?://','',proxy).split('/')[0].replace('{{port}}','9003')
 host=re.sub(r'-[0-9]+\.cnb\.run$','-9003.cnb.run',host)
 assert re.fullmatch(r'[A-Za-z0-9-]+-9003\.cnb\.run',host)
 pathlib.Path('/tmp/kebab-strelay-endpoint.json').write_text(json.dumps({'host':host}))
def terminate():
 for p in children.values():
  if p.poll() is None:p.terminate()
 for p in children.values():
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:p.kill();p.wait()
 children.clear()
try:
 while alive:
  f=STATE/'config.json'
  if not f.exists():time.sleep(2);continue
  raw=f.read_bytes();new=hashlib.sha256(raw).hexdigest()
  if new!=fingerprint:terminate();fingerprint=new
  c=json.loads(raw)
  jobs={
   'ssh':[str(BIN),'serve','--port','9001','--tun-secret',c['ssh_secret'],'--fwd-token',c['ssh_token']],
   'http':[str(BIN),'serve','--port','9002','--tun-secret',c['http_secret'],'--fwd-token',c['http_token']],
   'web':[str(BIN),'forward','--host','127.0.0.1','--port','9002','--token',c['http_token'],'--listen','0.0.0.0:9003','--plain','--no-gui']}
  for name,args in jobs.items():
   if name not in children or children[name].poll() is not None:
    children[name]=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  time.sleep(2)
finally:terminate()
