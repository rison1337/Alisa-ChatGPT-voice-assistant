package main

import (
    "log"
    "os"
    "os/exec"
    "os/signal"
    "syscall"
)

func main() {
    if err := os.MkdirAll("/tmp/yam", 0700); err != nil { log.Fatal(err) }
    lock, err := os.OpenFile("/tmp/yam/yam.lock", os.O_CREATE|os.O_RDWR, 0600)
    if err != nil { log.Fatal(err) }
    _ = lock.Close()
    cmd := exec.Command("/data/yam/bin/luna", "--listen", "127.0.0.1:8100", "--data-dir", "/data/yam/var/luna/data", "--yam-root", "/data/yam", "--yam-run", "/tmp/yam")
    cmd.Stdout = os.Stdout
    cmd.Stderr = os.Stderr
    if err := cmd.Start(); err != nil { log.Fatal(err) }
    signals := make(chan os.Signal, 1)
    signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
    done := make(chan error, 1)
    go func() { done <- cmd.Wait() }()
    select {
    case <-signals:
        _ = cmd.Process.Signal(syscall.SIGTERM)
        <-done
    case err := <-done:
        if err != nil { log.Fatal(err) }
    }
}
