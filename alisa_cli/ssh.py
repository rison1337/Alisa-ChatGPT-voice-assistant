import hashlib,shlex,subprocess,sys
from pathlib import Path
from .state import HOME,write_private,validate
from .errors import PublicError

def check_result(result):
    if not result.returncode:return
    # Only fixed messages cross the UI boundary; arbitrary SSH output is private.
    error=result.stderr.decode(errors='replace')
    if 'Host key verification failed' in error:raise PublicError('ssh_hostkey')
    if 'Permission denied' in error:raise PublicError('ssh_auth')
    if result.returncode==255:raise PublicError('ssh_unreachable')
    raise RuntimeError(f'Команда на колонке завершилась с кодом {result.returncode}')

class Station:
    def __init__(self,config):self.config=config
    def args(self,interactive=False):
        validate(self.config)
        c=self.config;a=['ssh','-T','-o','ConnectTimeout=8','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',
            '-o','StrictHostKeyChecking=yes','-o','BatchMode='+('no' if interactive else 'yes')]
        if c.get('ssh_explicit'):a+=['-F',__import__('os').devnull]
        if c.get('known_hosts'):a+=['-o','UserKnownHostsFile="'+str(c['known_hosts']).replace('\\','\\\\').replace('"','\\"')+'"']
        if c.get('port'):a+=['-p',str(c['port'])]
        if c.get('identity'):a+=['-i',str(c['identity'])]
        if c.get('user'):a+=['-l',c['user']]
        return a+[c['host']]
    def run(self,command,data=None,check=True,timeout=60):
        if sys.platform=='win32':
            validate(self.config)
            from .winssh import run
            return run(self.config,command,data,check,timeout)
        stdin={'input':data} if data is not None else {'stdin':subprocess.DEVNULL}
        try:result=subprocess.run(self.args()+[command],capture_output=True,timeout=timeout,**stdin)
        except subprocess.TimeoutExpired:raise PublicError('ssh_timeout') from None
        if check:check_result(result)
        return result
    def text(self,command,**kwargs):return self.run(command,**kwargs).stdout.decode()
    def open_input(self,command):
        if sys.platform=='win32':
            from .winssh import Stream
            return Stream(self.config,command)
        return subprocess.Popen(self.args()+[command],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def forward_local(self,port):
        if sys.platform=='win32':
            from .winssh import Forward
            return Forward(self.config,port)
        args=self.args()
        return subprocess.Popen(args[:-1]+['-N','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:{port}:127.0.0.1:18081',args[-1]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def put(self,path,data,mode=0o600):
        q=shlex.quote(path);temporary=shlex.quote(path+'.new');parent=shlex.quote(path.rsplit('/',1)[0])
        expected=hashlib.sha256(data).hexdigest()
        # An interrupted SSH input can look like normal EOF to remote cat.
        # Verify the complete candidate before replacing an existing file.
        command=f'''set -eu
umask 077
mkdir -p {parent}
trap {shlex.quote('rm -f '+temporary)} EXIT
cat > {temporary}
[ "$(wc -c < {temporary})" -eq {len(data)} ]
[ "$(sha256sum {temporary} | cut -d ' ' -f 1)" = {expected} ]
chmod {mode:o} {temporary}
sync
mv {temporary} {q}
sync
trap - EXIT
'''
        self.run(command,data=data,timeout=max(60,60+len(data)//(128*1024)))
        digest=self.text('sha256sum '+q).split()[0]
        if digest!=hashlib.sha256(data).hexdigest():raise RuntimeError('Контрольная сумма на колонке не совпала')
    def configure_bridge(self):
        c=self.config
        path=HOME/'bridge/ssh.config'
        # Forward the chosen connection without modifying ~/.ssh/config.
        def quote(value):return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'
        # An alias with no overrides retains its settings via the regular config.
        if not any(c.get(k) for k in ['port','identity','user']):return c['host'],None
        args=self.args();args.insert(1,'-G')
        effective=subprocess.run(args,capture_output=True,check=True).stdout.decode()
        settings={}
        for line in effective.splitlines():
            key,_,value=line.partition(' ')
            settings.setdefault(key,[]).append(value)
        lines=['Host alisa-cli']
        # Expand the original alias before replacing its name. Keep transport,
        # authentication and host-key identity, including ProxyJump/ProxyCommand.
        allowed=['hostname','port','user','identityfile','hostkeyalias','userknownhostsfile','globalknownhostsfile',
                 'proxyjump','proxycommand','identitiesonly','identityagent','hostkeyalgorithms','pubkeyacceptedalgorithms']
        for key in allowed:
            for value in settings.get(key,[]):
                if value=='none' and key in ('proxyjump','proxycommand','hostkeyalias'):continue
                # OpenSSH's -G output uses native config syntax for these lists
                # and commands. Newlines cannot enter via validated CLI fields.
                lines.append('  '+key+' '+value)
        lines+=['  StrictHostKeyChecking yes','  BatchMode yes']
        if not c.get('ssh_explicit'):lines+=['Host *','Include '+quote(Path.home()/'.ssh/config')]
        write_private(path,('\n'.join(lines)+'\n').encode());return 'alisa-cli',str(path)
