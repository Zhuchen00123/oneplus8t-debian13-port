"""Receive newly generated tunnel credentials over verified SSH stdin."""
import pathlib,json,os,sys,subprocess,fcntl,socket,time,hashlib
HERE=pathlib.Path(__file__).resolve().parent;STATE=pathlib.Path.home()/'.kebab-strelay'
STATE.mkdir(mode=0o700,exist_ok=True);os.chmod(STATE,0o700)
with (STATE/'lock').open('w') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 c=json.loads(sys.stdin.read());assert set(c)=={'ssh_secret','ssh_token','http_secret','http_token'}
 for value in c.values():assert len(value)==64 and all(x in '0123456789abcdef' for x in value)
 raw=json.dumps(c,sort_keys=True).encode();target=STATE/'config.json'
 changed=not target.exists() or target.read_bytes()!=raw
 if changed:
  temp=STATE/'config.new';temp.write_bytes(raw);os.chmod(temp,0o600);temp.replace(target)
 pidfile=STATE/'supervisor.pid';running=False
 def supervisor_process(pid):
  proc=pathlib.Path('/proc')/str(pid)
  if proc.stat().st_uid!=os.getuid():return False
  argv=(proc/'cmdline').read_bytes().split(b'\0');script=pathlib.Path(os.fsdecode(argv[1]))
  if not script.is_absolute():script=(proc/'cwd').resolve()/script
  return script.resolve()==(HERE/'supervisor.py').resolve()
 if pidfile.exists():
  try:
   running=supervisor_process(int(pidfile.read_text()))
  except (ValueError,OSError,IndexError):pass
 if not running:
  matches=[]
  for proc in pathlib.Path('/proc').glob('[0-9]*'):
   try:
    if supervisor_process(int(proc.name)):matches.append(int(proc.name))
   except (OSError,ValueError,IndexError):pass
  if len(matches)>1:raise RuntimeError('Ambiguous supervisor processes')
  if matches:
   running=True;pidfile.write_text(str(matches[0]));os.chmod(pidfile,0o600)
 if not running:
  # Never take over an unknown listener.
  for port in [9001,9002,9003]:
   sock=socket.socket()
   try:sock.bind(('0.0.0.0',port))
   finally:sock.close()
  p=subprocess.Popen([sys.executable,str(HERE/'supervisor.py')],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  # The supervisor owns its PID file; do not overwrite a stage process's PID
  # with a short-lived duplicate that exits after the exclusive-lock check.
 ready=False
 for _ in range(40):
  try:
   for port in [9001,9002,9003]:socket.create_connection(('127.0.0.1',port),timeout=.3).close()
   ready=True;break
  except OSError:time.sleep(.25)
 print(json.dumps({'ready':ready,'new_supervisor':not running,'configuration_changed':changed,'configuration_digest':hashlib.sha256(raw).hexdigest()}))
