"""Small bounded loopback TCP forwarder for the authenticated mcbridge protocol."""
import importlib.util,pathlib,socket,threading,time
spec=importlib.util.spec_from_file_location('wsbridge',pathlib.Path(__file__).with_name('ws_client.py'))
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
class Forwarder:
 def __init__(self,host,token,port=0):
  self.up=bridge.WsStream.connect(host,443,'/f/'+token+'/st-maintenance/up')
  self.down=bridge.WsStream.connect(host,443,'/f/'+token+'/st-maintenance/down')
  self.listener=socket.socket();self.listener.bind(('127.0.0.1',port));self.listener.listen();self.listener.settimeout(.5)
  self.port=self.listener.getsockname()[1];self.alive=threading.Event();self.alive.set();self.lock=threading.Lock();self.conns={};self.seq=1;self.threads=[]
  for fn in [self.accept,self.read,self.ping]:self.thread(fn)
 def thread(self,fn,*args):
  t=threading.Thread(target=fn,args=args,daemon=True);self.threads.append(t);t.start()
 def send(self,typ,i,b=b''):self.up.send(bytes([typ])+i.to_bytes(4,'big')+b)
 def accept(self):
  try:
   while self.alive.is_set():
    try:s,_=self.listener.accept()
    except socket.timeout:continue
    s.settimeout(15)
    with self.lock:i=self.seq;self.seq+=1;self.conns[i]=s
    self.send(1,i);self.thread(self.upload,i,s)
  except OSError:pass
 def upload(self,i,s):
  try:
   while self.alive.is_set():
    try:b=s.recv(16384)
    except socket.timeout:continue
    if not b:break
    self.send(2,i,b)
  except Exception:pass
  finally:
   try:self.send(3,i)
   except Exception:pass
   with self.lock:self.conns.pop(i,None)
   s.close()
 def read(self):
  try:
   while self.alive.is_set():
    frame=self.down.recv_message()
    if frame is None:break
    if len(frame)<5:continue
    typ=frame[0];i=int.from_bytes(frame[1:5],'big')
    if typ==4:self.send(5,i);continue
    with self.lock:s=self.conns.get(i)
    if s and typ==2:s.sendall(frame[5:])
    elif s and typ==3:
     try:s.shutdown(socket.SHUT_RDWR)
     except OSError:pass
  except Exception:pass
  finally:self.alive.clear()
 def ping(self):
  while self.alive.is_set():
   for _ in range(20):
    if not self.alive.is_set():return
    time.sleep(1)
   try:self.send(4,0)
   except Exception:self.alive.clear();return
 def close(self):
  self.alive.clear();self.listener.close()
  for ws in [self.up,self.down]:
   try:ws.sock.shutdown(socket.SHUT_RDWR)
   except OSError:pass
   ws.sock.close()
  with self.lock:
   for s in self.conns.values():s.close()
  for t in self.threads:t.join(timeout=2)
