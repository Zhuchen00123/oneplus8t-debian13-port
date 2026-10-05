"""Discover the dedicated relay address without USB; never print credentials."""
import pathlib,json,urllib.request,shlex,socket,re,logging
import paramiko
ROOT=pathlib.Path(__file__).resolve().parents[2]
PRIVATE=ROOT/'local/sillytavern-migration-20261005'
logging.getLogger('paramiko').setLevel(logging.CRITICAL)
def discover():
 cfg=json.loads((PRIVATE/'relay-private.json').read_text(encoding='utf8'))
 repo=json.loads((PRIVATE/'relay-publish.json').read_text(encoding='utf8'))['repo']
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def api(path):
  req=urllib.request.Request('https://api.cnb.cool'+path,headers={'Authorization':'Bearer '+cfg['cnb_token']})
  with opener.open(req,timeout=20) as r:return json.load(r)
 rows=api('/workspace/list').get('list',[])
 rows=[r for r in rows if r.get('status')=='running' and repo in json.dumps(r)]
 if len(rows)!=1:raise RuntimeError('Dedicated environment is recovering or unavailable')
 detail=api('/'+repo+'/-/workspace/detail/'+rows[0]['sn']);parts=shlex.split(detail['ssh']);port=22;dest=None
 if not parts or parts[0]!='ssh':raise ValueError('Unexpected SSH metadata')
 for i,v in enumerate(parts[1:],1):
  if v=='-p':port=int(parts[i+1])
  elif '@' in v and not v.startswith('-'):dest=v
 if not dest or not re.fullmatch(r'[A-Za-z0-9_.@-]+',dest):raise ValueError('Invalid SSH destination')
 user,host=dest.rsplit('@',1)
 known=paramiko.HostKeys();known.load(str(PRIVATE/'cnb-known-hosts'));expected=known.lookup('kebab-cnb-relay')
 t=paramiko.Transport(socket.create_connection((host,port),timeout=15))
 try:
  t.start_client(timeout=15);key=t.get_remote_server_key()
  if not expected or expected.get(key.get_name())!=key:raise RuntimeError('CNB SSH host key changed; verification required')
  t.auth_none(user);ch=t.open_session(timeout=15);ch.exec_command('cat /tmp/kebab-strelay-endpoint.json')
  data=ch.makefile('rb').read()
  if ch.recv_exit_status():raise RuntimeError('Dedicated relay not ready')
  host=json.loads(data)['host']
  if not re.fullmatch(r'[A-Za-z0-9-]+-9003\.cnb\.run',host):raise ValueError('Invalid endpoint')
 finally:t.close()
 return {'https_url':'https://'+host+'/','ssh_wss_host':host.removesuffix('-9003.cnb.run')+'-9001.cnb.run'}
if __name__=='__main__':
 try:print(discover()['https_url'])
 except Exception as e:raise SystemExit('Relay address unavailable ('+type(e).__name__+'); retry after recovery.')
