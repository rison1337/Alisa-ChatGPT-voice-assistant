"""Authentication fallback must not bypass host-key or dedicated-key checks."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from alisa_cli import winssh


class NoneAuthentication(unittest.TestCase):
    def exercise(self,accepted=True,allow_none=True,bad_host=False):
        class AuthError(Exception): pass
        class HostError(Exception): pass
        class SSHError(Exception): pass
        transport=Mock()
        transport.is_authenticated.return_value=accepted
        if not accepted:transport.auth_none.side_effect=AuthError()
        fallback=Mock()
        class Client:
            def load_host_keys(self,path): pass
            def set_missing_host_key_policy(self,policy): pass
            def get_transport(self):return transport
            def connect(self,host,**kwargs):
                if bad_host:raise HostError()
                self._auth(kwargs['username'])
            def _auth(self,*args,**kwargs):fallback()
            def close(self): pass
        library=types.SimpleNamespace(SSHClient=Client,RejectPolicy=Mock,
            AuthenticationException=AuthError,BadHostKeyException=HostError,SSHException=SSHError)
        with tempfile.TemporaryDirectory() as folder,patch.object(winssh,'library',return_value=library):
            known=Path(folder)/'known_hosts';known.touch()
            config={'host':'192.0.2.10','known_hosts':str(known),'_allow_none':allow_none}
            if bad_host:
                with self.assertRaises(Exception):winssh.connect(config)
            else:winssh.connect(config)
        return transport,fallback

    def test_none_accepted(self):
        transport,fallback=self.exercise()
        transport.auth_none.assert_called_once_with('root');fallback.assert_not_called()

    def test_rejection_falls_back(self):
        _,fallback=self.exercise(accepted=False);fallback.assert_called_once()

    def test_dedicated_key_verification_cannot_use_none(self):
        transport,fallback=self.exercise(allow_none=False)
        transport.auth_none.assert_not_called();fallback.assert_called_once()

    def test_bad_host_precedes_authentication(self):
        transport,fallback=self.exercise(bad_host=True)
        transport.auth_none.assert_not_called();fallback.assert_not_called()
