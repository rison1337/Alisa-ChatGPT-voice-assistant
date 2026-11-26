#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <grp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ipc.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/sem.h>
#include <unistd.h>
#define BASE "/bkupgrade/data/street-alisa-port-v1"
#define STOP BASE "/run/HOLDER_STOP"
static volatile sig_atomic_t stop;
static void on_signal(int sig) { (void)sig; stop=1; }
static void silence(const char *f,int l,const char *s,int e,const char *fmt,...) { (void)f;(void)l;(void)s;(void)e;(void)fmt; }
static int report(int rc,unsigned opens,unsigned losses) {
    char b[80]; int n=snprintf(b,sizeof b,"rc=%+011d opens=%010u losses=%010u\n",rc,opens,losses);
    return n>0 && n<(int)sizeof b && pwrite(3,b,n,0)==n;
}
int main(int argc,char **argv) {
    (void)argv;
    gid_t g[]={29,104}; struct rlimit r={0,0};
    if (argc!=1 || getuid()!=0 || fcntl(3,F_GETFD)<0 || clearenv() || setenv("HOME","/nonexistent",1) || setrlimit(RLIMIT_CORE,&r) || setgroups(2,g) || setresgid(29,29,29) || setresuid(48261,48261,48261) || prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)) return 77;
    struct sigaction sa={0}; sa.sa_handler=on_signal; sigemptyset(&sa.sa_mask);
    if (sigaction(SIGTERM,&sa,NULL) || sigaction(SIGINT,&sa,NULL)) return 77;
    void *lib=dlopen("libasound.so.2",RTLD_NOW|RTLD_LOCAL);
    if (!lib) return 78;
    int (*pcm_open)(void **,const char *,int,int)=dlsym(lib,"snd_pcm_open");
    int (*pcm_close)(void *)=dlsym(lib,"snd_pcm_close");
    int (*pcm_state)(void *)=dlsym(lib,"snd_pcm_state");
    int (*err_handler)(void (*)(const char *,int,const char *,int,const char *,...))=dlsym(lib,"snd_lib_error_set_handler");
    if (!pcm_open || !pcm_close || !pcm_state || !err_handler) return 78;
    err_handler(silence);
    void *pcm=NULL; int sem=-1, rc=-1, delay=1, remaining=0; unsigned opens=0,losses=0;
    if (!report(rc,opens,losses)) return 79;
    while (!stop && access(STOP,F_OK)) {
        if (pcm && (semget(1028,0,0)!=sem || pcm_state(pcm)==8 /* DISCONNECTED */)) {
            pcm_close(pcm); pcm=NULL; losses++; remaining=0;
            if (!report(-19,opens,losses)) return 79;
        }
        if (!pcm && remaining--<=0) {
            rc=pcm_open(&pcm,"speakers",0 /* PLAYBACK */,1 /* NONBLOCK */);
            if (rc>=0 && (sem=semget(1028,0,0))<0) { pcm_close(pcm); rc=-19; }
            if (rc>=0) { opens++; delay=1; } else { pcm=NULL; remaining=delay; if (delay<8) delay*=2; }
            if (!report(rc,opens,losses)) { if (pcm) pcm_close(pcm); return 79; }
        }
        sleep(1);
    }
    if (pcm) pcm_close(pcm);
    return report(-125,opens,losses)?0:79;
}
