#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
int main(void) {
    for (int i = 0; i < 3; ++i) {
        void *lib = dlopen("libasound.so.2", RTLD_NOW | RTLD_LOCAL);
        if (!lib) { puts(dlerror()); return 1; }
        int (*open_pcm)(void **,const char *,int,int) = dlsym(lib,"snd_pcm_open");
        int (*close_pcm)(void *) = dlsym(lib,"snd_pcm_close");
        void *write_pcm = dlsym(lib,"snd_pcm_writei");
        Dl_info info;
        if (!open_pcm || !close_pcm || !write_pcm || !dladdr(write_pcm,&info)) return 2;
        printf("cycle %d: %s\n", i+1, info.dli_fname);
        void *pcm = 0;
        int result = open_pcm(&pcm,"null",0,0);
        if (result < 0) { printf("null open: %d\n",result); return 3; }
        close_pcm(pcm);
        dlclose(lib);
    }
    return 0;
}
