"""Authenticated local pipe; passwords never enter config or worker arguments."""
import json,os,re

ENVIRONMENT='ALISA_WINDOWS_SSH_MEMORY'

def request(key,operation,password=None):
    raw=os.environ.get(ENVIRONMENT)
    if not raw:return None
    try:
        value=json.loads(raw);address=value['address'];authkey=value['authkey']
        if (not isinstance(address,str) or not re.fullmatch(r'\\\\\.\\pipe\\alisa-voice-[0-9a-f]{32}',address) or
            not isinstance(authkey,str) or not re.fullmatch('[0-9a-f]{64}',authkey)):
            return None
        from multiprocessing.connection import Client
        from multiprocessing import AuthenticationError
        try:
            with Client(address,family='AF_PIPE',authkey=bytes.fromhex(authkey)) as connection:
                message={'operation':operation,'key':list(key)}
                if password is not None:message['password']=password
                connection.send_bytes(json.dumps(message).encode('utf-8'))
                if not connection.poll(3):return None
                reply=json.loads(connection.recv_bytes(65536).decode('utf-8'))
                return reply if isinstance(reply,dict) and reply.get('ok') is True else None
        except (OSError,EOFError,AuthenticationError):return None
    except (ValueError,KeyError,TypeError):return None
