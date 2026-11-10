"""Session-only SSH memory in the immutable Windows supervisor, outside app."""
import json,os,re,threading,uuid
from multiprocessing.connection import Listener,Client
from multiprocessing import AuthenticationError

ENVIRONMENT='ALISA_WINDOWS_SSH_MEMORY'

class Broker:
    def __init__(self):
        self.address=r'\\.\pipe\alisa-voice-'+uuid.uuid4().hex
        self.authkey=os.urandom(32)
        self.listener=Listener(self.address,family='AF_PIPE',authkey=self.authkey)
        self.values={};self.thread=threading.Thread(target=self.serve,daemon=True);self.thread.start()

    def specification(self):
        return json.dumps({'address':self.address,'authkey':self.authkey.hex()})

    def serve(self):
        try:
            while True:
                try:connection=self.listener.accept()
                except AuthenticationError:continue
                except (OSError,EOFError):break
                with connection:
                    try:
                        if not connection.poll(5):continue
                        request=json.loads(connection.recv_bytes(65536).decode('utf-8'))
                        if not isinstance(request,dict):continue
                        operation=request.get('operation')
                        if operation=='stop':
                            connection.send_bytes(b'{"ok":true}');break
                        if operation=='ping':connection.send_bytes(b'{"ok":true}');continue
                        key=request.get('key')
                        if (not isinstance(key,list) or len(key)!=4 or not isinstance(key[0],str) or
                            not isinstance(key[1],int) or isinstance(key[1],bool) or not isinstance(key[2],str) or
                            not (key[3] is None or isinstance(key[3],str))):
                            connection.send_bytes(b'{"ok":false}');continue
                        key=tuple(key)
                        if operation=='get':reply={'ok':True,'password':self.values.get(key)}
                        elif operation=='put' and isinstance(request.get('password'),str):
                            self.values[key]=request['password'];reply={'ok':True}
                        elif operation=='clear':self.values.pop(key,None);reply={'ok':True}
                        else:reply={'ok':False}
                        connection.send_bytes(json.dumps(reply).encode('utf-8'))
                    except (OSError,EOFError,ValueError,TypeError):continue
        finally:
            self.values.clear();self.listener.close()

    def close(self):
        try:
            with Client(self.address,family='AF_PIPE',authkey=self.authkey) as connection:
                connection.send_bytes(b'{"operation":"stop"}')
                if connection.poll(3):connection.recv_bytes(65536)
        except (OSError,EOFError,AuthenticationError):pass
        self.thread.join(timeout=6)
        self.values.clear();self.listener.close()

def session(inherited=None):
    """A delegated supervisor can reuse the still-running parent's memory."""
    if inherited:
        try:
            value=json.loads(inherited);address=value['address'];authkey=value['authkey']
            if (isinstance(address,str) and re.fullmatch(r'\\\\\.\\pipe\\alisa-voice-[0-9a-f]{32}',address) and
                isinstance(authkey,str) and re.fullmatch('[0-9a-f]{64}',authkey)):
                with Client(address,family='AF_PIPE',authkey=bytes.fromhex(authkey)) as connection:
                    connection.send_bytes(b'{"operation":"ping"}')
                    if connection.poll(3) and json.loads(connection.recv_bytes(65536)).get('ok') is True:
                        return None,inherited
        except (OSError,EOFError,ValueError,KeyError,TypeError,AuthenticationError):pass
    broker=Broker()
    return broker,broker.specification()
