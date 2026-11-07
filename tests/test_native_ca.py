"""Real local TLS verifies the shared native CA selection without device access."""
import http.server
import os
from pathlib import Path
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='linux' and (ROOT/'engines/native/native/ca_store.hpp').is_file()
                     and all(shutil.which(tool) for tool in ('c++','pkg-config','openssl')),
                     'Linux C++/libcurl and OpenSSL test server')
class NativeCa(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.root=Path(cls.temp.name);cls.check=cls.root/'check'
        flags=subprocess.run(['pkg-config','--cflags','--libs','libcurl'],capture_output=True,text=True)
        if flags.returncode:raise unittest.SkipTest('libcurl development files missing')
        subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(ROOT/'engines/native/native'),
                        str(ROOT/'tests/check_native_ca.cpp'),*shlex.split(flags.stdout),'-o',str(cls.check)],check=True,capture_output=True)
        for name in ('server','other'):
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                            '-keyout',str(cls.root/(name+'.key')),'-out',str(cls.root/(name+'.crt')),
                            '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-days','1'],
                           check=True,capture_output=True)
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    body=b'local TLS test';self.send_response(200);self.send_header('Content-Length',str(len(body)))
                    self.end_headers();self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass
            def log_message(self,*args):pass
        cls.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.root/'server.crt',cls.root/'server.key')
        cls.server.socket=context.wrap_socket(cls.server.socket,server_side=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,kwargs={'poll_interval':.05},daemon=True);cls.thread.start()
        cls.addClassCleanup(cls.server.server_close);cls.addClassCleanup(cls.thread.join,3);cls.addClassCleanup(cls.server.shutdown)
        cls.url='https://localhost:'+str(cls.server.server_port)+'/'

    def run_check(self,mode='fetch',overrides=None,first='absent.crt',second='server.crt',url=None):
        env={key:value for key,value in os.environ.items() if key not in ('ALISA_CA_FILE','SSL_CERT_FILE','SSL_CERT_DIR')}
        env.update({key:str(value) for key,value in (overrides or {}).items()})
        return subprocess.run([self.check,mode,url or self.url,self.root/first,self.root/second],env=env,capture_output=True,timeout=5)

    def test_fallback_bundle_and_first_bundle_have_deterministic_order(self):
        self.assertEqual(self.run_check().returncode,0)
        self.assertEqual(self.run_check('select').stdout.decode().splitlines()[0],str(self.root/'server.crt'))
        self.assertEqual(self.run_check('select',first='other.crt').stdout.decode().splitlines()[0],str(self.root/'other.crt'))
        self.assertNotEqual(self.run_check(first='other.crt').returncode,0)

    def test_alisa_override_precedes_ssl_override_and_missing_override_does_not_fall_back(self):
        self.assertEqual(self.run_check(overrides={'ALISA_CA_FILE':self.root/'server.crt','SSL_CERT_FILE':self.root/'other.crt'}).returncode,0)
        self.assertEqual(self.run_check(overrides={'SSL_CERT_FILE':self.root/'server.crt'},second='other.crt').returncode,0)
        self.assertNotEqual(self.run_check(overrides={'ALISA_CA_FILE':self.root/'missing.crt'}).returncode,0)
        self.assertNotEqual(self.run_check(overrides={'SSL_CERT_FILE':self.root/'missing.crt'}).returncode,0)

    def test_tls_verification_stays_enabled_for_untrusted_chain_and_wrong_hostname(self):
        self.assertEqual(self.run_check(overrides={'SSL_CERT_FILE':self.root/'other.crt'}).returncode,60)
        self.assertEqual(self.run_check(url=self.url.replace('localhost','127.0.0.1')).returncode,60)

    def test_empty_overrides_use_fallback_and_directories_are_not_ca_files(self):
        self.assertEqual(self.run_check(overrides={'ALISA_CA_FILE':'','SSL_CERT_FILE':''},first='.').returncode,0)

    def test_ssl_cert_dir_is_used_as_a_hashed_trust_store(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);shutil.copy2(self.root/'server.crt',folder/'ca.pem')
            subprocess.run(['openssl','rehash',str(folder)],check=True,capture_output=True)
            self.assertEqual(self.run_check(overrides={'SSL_CERT_DIR':folder},first='absent.crt',second='absent-too.crt').returncode,0)
