"""Keep a loopback SSH forwarder connected across relay rebuilds."""
import argparse,json,time,threading,signal
from access import discover,PRIVATE
from wss_forwarder import Forwarder
ap=argparse.ArgumentParser();ap.add_argument('--listen-port',type=int,default=2222);a=ap.parse_args()
cfg=json.loads((PRIVATE/'relay-private.json').read_text(encoding='utf8'));stop=threading.Event()
def halt(*_):stop.set()
signal.signal(signal.SIGINT,halt);signal.signal(signal.SIGTERM,halt)
last=None
while not stop.is_set():
 f=None
 try:
  endpoint=discover();f=Forwarder(endpoint['ssh_wss_host'],cfg['ssh_token'],a.listen_port)
  if last!='connected':print('SSH forward ready at 127.0.0.1:'+str(a.listen_port),flush=True);last='connected'
  while f.alive.is_set() and not stop.wait(1):pass
 except Exception as e:
  state='reconnecting:'+type(e).__name__
  if state!=last:print(state,flush=True);last=state
 finally:
  if f:f.close()
 stop.wait(5)
