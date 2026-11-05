#define _DEFAULT_SOURCE
#include <juice/juice.h>
#include <arpa/inet.h>
#include <assert.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static const char *current_ipv4;
static unsigned wildcard_binds;
int __real_bind(int, const struct sockaddr *, socklen_t);
int __wrap_bind(int fd, const struct sockaddr *addr, socklen_t len) {
    if (addr->sa_family == AF_INET && ((const struct sockaddr_in *)addr)->sin_addr.s_addr == INADDR_ANY)
        wildcard_binds++;
    return __real_bind(fd, addr, len);
}
int __wrap_getifaddrs(struct ifaddrs **out) {
    static struct ifaddrs interfaces[2];
    static struct sockaddr_in v4;
    static struct sockaddr_in6 v6;
    memset(interfaces, 0, sizeof(interfaces)); memset(&v4, 0, sizeof(v4)); memset(&v6, 0, sizeof(v6));
    v4.sin_family = AF_INET; assert(inet_pton(AF_INET, current_ipv4, &v4.sin_addr) == 1);
    v6.sin6_family = AF_INET6; assert(inet_pton(AF_INET6, "2001:db8::10", &v6.sin6_addr) == 1);
    interfaces[0].ifa_next = &interfaces[1];
    interfaces[0].ifa_name = interfaces[1].ifa_name = "fixture0";
    interfaces[0].ifa_flags = interfaces[1].ifa_flags = IFF_UP | IFF_RUNNING;
    interfaces[0].ifa_addr = (struct sockaddr *)&v4; interfaces[1].ifa_addr = (struct sockaddr *)&v6;
    *out = interfaces; return 0;
}
void __wrap_freeifaddrs(struct ifaddrs *p) {(void)p;}
struct result { atomic_bool done; unsigned candidates; };
static void candidate(juice_agent_t *agent, const char *sdp, void *user) {
    (void)agent; struct result *r = user;
    assert(strstr(sdp, current_ipv4)); assert(strstr(sdp, " typ host"));
    assert(!strstr(sdp, "2001:db8")); assert(!strstr(sdp, " 0.0.0.0 "));
    r->candidates++; printf("host candidate: %s\n", sdp);
}
static void gathered(juice_agent_t *agent, void *user) {
    (void)agent; struct result *r = user; atomic_store(&r->done, 1);
}
static void run(const char *address) {
    current_ipv4 = address;
    struct result r = {0}; juice_config_t config = {0};
    config.bind_address = "0.0.0.0"; config.cb_candidate = candidate;
    config.cb_gathering_done = gathered; config.user_ptr = &r;
    juice_agent_t *agent = juice_create(&config); assert(agent);
    assert(juice_gather_candidates(agent) == 0);
    for (unsigned i = 0; i < 200 && !atomic_load(&r.done); i++) usleep(10000);
    assert(atomic_load(&r.done)); juice_destroy(agent); assert(r.candidates == 1);
}
int main(void) {
    juice_set_log_level(JUICE_LOG_LEVEL_NONE);
    run("192.0.2.44"); run("198.51.100.28");
    assert(wildcard_binds >= 2);
    puts("PASS: IPv4 wildcard gathers current IPv4 after address change; no IPv6 or wildcard host candidate.");
}
