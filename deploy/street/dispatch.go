package main

import (
	"os"
	"strings"
	"syscall"
)

const client = "/bkupgrade/data/street-alisa-port-v1/client.armhf"
const state = "/bkupgrade/data/street-alisa-port-v1/state"

func must(err error) {
	if err != nil {
		os.Exit(77)
	}
}
func main() {
	if len(os.Args) != 2 || os.Geteuid() != 0 {
		os.Exit(64)
	}
	action := os.Args[1]
	switch action {
	case "info", "serve", "status", "close", "vpn-health":
	default:
		os.Exit(64)
	}
	must(syscall.Setrlimit(syscall.RLIMIT_CORE, &syscall.Rlimit{}))
	must(syscall.Setgroups([]int{29, 104}))
	must(syscall.Setresgid(29, 29, 29))
	must(syscall.Setresuid(48261, 48261, 48261))
	b, e := os.ReadFile("/proc/self/status")
	must(e)
	want := map[string]string{"Uid:": "48261 48261 48261 48261", "Gid:": "29 29 29 29", "Groups:": "29 104", "CapEff:": "0000000000000000", "CapPrm:": "0000000000000000", "NoNewPrivs:": "1"}
	for _, l := range strings.Split(string(b), "\n") {
		f := strings.Fields(l)
		if len(f) > 0 {
			if x, ok := want[f[0]]; ok {
				if strings.Join(f[1:], " ") != x {
					os.Exit(78)
				}
				delete(want, f[0])
			}
		}
	}
	if len(want) > 0 {
		os.Exit(78)
	}
	syscall.Umask(077)
	must(syscall.Exec(client, []string{client, action, state}, []string{"PATH=/usr/bin:/bin", "HOME=/bkupgrade/data/street-alisa-port-v1/run/client", "LD_PRELOAD=/bkupgrade/data/street-alisa-port-v1/loopback.so", "ALISA_DISABLE_LED=1", "ALISA_DISABLE_STUN=1", "ALISA_PROXY_URL=socks5h://localhost/bkupgrade/data/street-alisa-port-v1/run/relay.sock"}))
}
