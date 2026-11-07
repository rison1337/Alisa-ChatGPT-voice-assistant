"""Run the native C agent against a local IPC peer, without station audio."""
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


def number(value):
    result=bytearray()
    while value>=128:result.append((value&127)|128);value>>=7
    result.append(value);return bytes(result)


def fields(data):
    result={};offset=0
    def read():
        nonlocal offset
        value=0;shift=0
        while True:
            byte=data[offset];offset+=1;value|=(byte&127)<<shift
            if byte<128:return value
            shift+=7
    while offset<len(data):
        key=read()
        if key&7==0:value=read()
        elif key&7==2:
            length=read();value=data[offset:offset+length];offset+=length
        else:raise ValueError('Unexpected wire type')
        result[key>>3]=value
    return result


def field(key,data):return number(key*8+2)+number(len(data))+data


@unittest.skipUnless(sys.platform=='linux' and shutil.which('clang'),'Linux host C compiler')
class NativeMusic(unittest.TestCase):
    wire_format='wrapped'
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory();cls.library=Path(cls.build.name)/'agent.so'
        subprocess.run(['clang','-shared','-fPIC','-O2',str(ROOT/'engines/native/station/native_agent.c'),'-o',str(cls.library)],check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.build.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name)
        self.server=socket.socket();self.server.bind(('127.0.0.1',0));self.server.listen(2);self.server.settimeout(3)
        self.addCleanup(self.server.close)
        template=root/'focus.wav';template.write_bytes(b'synthetic focus template')
        self.process=subprocess.Popen(['/usr/bin/true'],bufsize=0,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={**os.environ,'LD_PRELOAD':str(self.library),'ALICE_NATIVE_AGENT':'1','ALICE_NATIVE_FOCUS':'1',
                 'ALICE_NATIVE_PORT':str(self.server.getsockname()[1]),'ALICE_NATIVE_LOCK':str(root/'lock'),
                 'ALICE_TTS_GATE_FILE':str(root/'lease'),'ALICE_FOCUS_TEMPLATE':str(template),'ALICE_FOCUS_DIRECTORY':str(root),
                 'ALICE_NATIVE_WIRE_FORMAT':self.wire_format})
        self.addCleanup(self.cleanup)
        self.peer,_=self.server.accept();self.peer.settimeout(1);self.addCleanup(self.peer.close)
        ready=self.message();self.assertEqual(ready['event'],'ready')
        self.assertEqual(ready['wire_format'],self.wire_format)

    def cleanup(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr):stream.close()

    def message(self):
        self.assertTrue(select.select([self.process.stdout],[],[],2)[0])
        return json.loads(self.process.stdout.readline())

    def control(self,value):
        self.process.stdin.write((value+'\n').encode());self.process.stdin.flush()
        return self.message()

    def state(self,value):
        envelope=self.envelope(3,number(8)+number(value))
        self.peer.sendall(len(envelope).to_bytes(4,'little')+envelope)
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if self.control('PING')['native_state']==value:return
        self.fail('Agent did not consume state')

    def exact(self,size):
        result=b''
        while len(result)<size:
            part=self.peer.recv(size-len(result));self.assertTrue(part);result+=part
        return result

    def rpc(self):
        envelope=fields(self.exact(int.from_bytes(self.exact(4),'little')))
        if self.wire_format=='fields':
            self.assertEqual(set(envelope),{210})
            return fields(envelope[210])
        self.assertEqual(envelope[253],210)
        return fields(envelope[252])

    def envelope(self,kind,payload):
        if self.wire_format=='fields':return field(kind,payload)
        return field(252,payload)+number(253*8)+number(kind)

    def test_focus_event_in_selected_format_grants_only_our_request(self):
        self.state(0);self.control('CHAT');self.assert_pause()
        play=fields(self.rpc()[12]);request=fields(play[4])[4]
        directive=field(4,request)
        event=field(1,field(1,directive))+field(3,number(8)+number(6)+number(17*8)+number(1))
        envelope=self.envelope(210,field(7,b'AudioFilePlayerCapability')+field(17,event))
        self.peer.sendall(len(envelope).to_bytes(4,'little')+envelope)
        report=self.message();self.assertEqual(report['event'],'focus');self.assertEqual(report['focus_ready'],1)
        self.assertEqual(self.control('IDLE')['focus_ready'],0)
        self.assertEqual(self.rpc()[7],b'AudioFilePlayerCapability')

    def test_recognized_command_uses_selected_format_for_queue_clear(self):
        request=field(1,'открой чат'.encode())+number(16)+number(1)+number(32)+number(1)
        state=number(8)+number(3)+field(7,b'00000000-0000-4000-8000-000000000042')+field(15,request)
        envelope=self.envelope(3,state)
        self.peer.sendall(len(envelope).to_bytes(4,'little')+envelope)
        self.assertEqual(self.message(),{'event':'command','action':'open'})
        self.assertEqual(self.rpc()[7],b'AliceCapability')
        self.assertEqual(self.control('PING')['commands'],1)

    def test_guard_uses_the_selected_format_after_agent_is_killed(self):
        self.state(0);self.control('CHAT');self.assert_pause()
        path=fields(self.rpc()[12])[2]
        self.process.kill();self.process.wait(timeout=3)
        guard,_=self.server.accept();self.addCleanup(guard.close);guard.settimeout(2)
        original=self.peer
        try:
            self.peer=guard
            remote=self.rpc();self.assertEqual(remote[7],b'AudioFilePlayerCapability')
            stop=fields(remote[12]);self.assertEqual(stop[1],1);self.assertEqual(stop[2],path)
        finally:self.peer=original

    def test_wrong_or_malformed_envelopes_do_not_change_state(self):
        payload=number(8)+number(3)
        wrong=field(3,payload) if self.wire_format=='wrapped' else field(252,payload)+number(253*8)+number(3)
        invalid=self.envelope(3,b'\x08\x80')
        ambiguous=field(3,payload)+field(252,payload)+number(253*8)+number(3)
        cases=[wrong,invalid,self.envelope(207,payload)]
        if self.wire_format=='fields':cases.append(ambiguous)
        for envelope in cases:
            self.peer.sendall(len(envelope).to_bytes(4,'little')+envelope)
            self.assertEqual(self.control('PING')['native_state'],-1)
        self.state(3)

    def assert_pause(self):
        remote=self.rpc();self.assertEqual(remote[7],b'PlaybackControlCapability')
        method=fields(remote[13]);self.assertEqual(method[1],1);self.assertEqual(method[7],0)

    def test_chat_pauses_music_once_before_acquiring_focus(self):
        self.state(0);self.control('CHAT');self.assert_pause()
        self.assertEqual(self.rpc()[7],b'AudioFilePlayerCapability')
        report=self.control('CHAT');self.assertEqual(report['music_pause_requests'],1)
        self.assertFalse(select.select([self.peer],[],[],.05)[0])

    def test_pause_waits_until_recognition_and_busy_finish_and_is_not_repeated(self):
        self.state(1);report=self.control('CHAT')
        self.assertEqual((report['music_pause_requests'],report['pause_pending']),(0,1))
        self.assertEqual(self.rpc()[7],b'AudioFilePlayerCapability')
        self.state(3);self.assertFalse(select.select([self.peer],[],[],.05)[0])
        self.state(0);self.assert_pause();self.state(0)
        self.assertEqual(self.control('PING')['music_pause_requests'],1)
        self.assertFalse(select.select([self.peer],[],[],.05)[0])

    def test_closing_before_idle_cancels_the_deferred_pause(self):
        self.state(1);self.control('CHAT');self.assertEqual(self.rpc()[7],b'AudioFilePlayerCapability')
        report=self.control('IDLE');self.assertEqual(report['pause_pending'],0)
        self.rpc()  # Release our focus only.
        self.state(0);self.assertEqual(self.control('PING')['music_pause_requests'],0)
        self.assertFalse(select.select([self.peer],[],[],.05)[0])

    def test_stock_music_is_untouched_until_chat_is_opened(self):
        for value in (1,3,0):self.state(value)
        self.assertEqual(self.control('PING')['music_pause_requests'],0)
        self.assertFalse(select.select([self.peer],[],[],.05)[0])


class NativeFields(NativeMusic):
    wire_format='fields'
