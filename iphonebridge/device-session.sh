#!/bin/sh
# A private, nonpersistent session. No launchd/package manager involvement.
set -eu
base=/var/mobile/Media/iPhoneBridge
active="$base/active"
mode=$1
token=$2
case "$token" in ''|*[!a-f0-9]*) exit 40;; esac
[ "${#token}" -eq 32 ] || exit 40

identity() { ps -p "$1" -o lstart=,command= 2>/dev/null || true; }

case "$mode" in
stop)
    [ -d "$active" ] || exit 0
    [ "$(cat "$active/token")" = "$token" ] || exit 41
    if [ -f "$active/pid" ] && [ -f "$active/identity" ]; then
        child=$(cat "$active/pid")
        case "$child" in ''|*[!0-9]*) exit 42;; esac
        current=$(identity "$child")
        if [ -n "$current" ]; then
            [ "$current" = "$(cat "$active/identity")" ] || exit 43
            kill -TERM "$child"
            n=0
            while [ -d "$active" ] && [ "$n" -lt 8 ]; do
                sleep 1
                n=$((n + 1))
            done
            [ ! -d "$active" ] || exit 44
        fi
    fi
    # The owner may have exited before writing the child identity.
    if [ -d "$active" ]; then
        owner=$(cat "$active/owner")
        [ -z "$(identity "$owner")" ] || exit 45
        rm -f "$active/pid" "$active/identity" "$active/owner" "$active/token"
        rmdir "$active"
    fi
    ;;
run)
    binary=$3
    case "$binary" in "$base"/trollvncserver-*) ;; *) exit 46;; esac
    umask 077
    mkdir "$active" || exit 47
    printf '%s\n' "$token" > "$active/token"
    printf '%s\n' "$$" > "$active/owner"
    child=
    own_child() {
        [ -n "$child" ] || return 1
        parent=$(ps -p "$child" -o ppid= 2>/dev/null | tr -d ' ' || true)
        [ "$parent" = "$$" ] || return 1
        state=$(ps -p "$child" -o stat= 2>/dev/null | tr -d ' ' || true)
        case "$state" in ''|Z*) return 1;; *) return 0;; esac
    }
    cleanup() {
        trap - EXIT HUP INT TERM
        if own_child; then
            kill -TERM "$child" 2>/dev/null || true
            n=0
            while own_child && [ "$n" -lt 5 ]; do
                sleep 1
                n=$((n + 1))
            done
            # Preserve metadata for diagnosis if graceful termination failed.
            if own_child; then
                printf '%s\n' 'Bridge daemon did not exit; ownership metadata retained' >&2
                exit 51
            fi
        fi
        if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
            # Never remove the last ownership evidence for an unknown live PID.
            printf '%s\n' 'Bridge child identity changed; ownership metadata retained' >&2
            exit 52
        fi
        rm -f "$active/pid" "$active/identity" "$active/owner" "$active/token"
        rmdir "$active"
    }
    trap cleanup EXIT
    trap 'exit 0' HUP INT TERM
    # Full resolution; precise dirty regions with no coalescing delay. Keep
    # blocking swaps and the upstream two-encode limit to preserve final frames.
    DISABLE_TWEAKS=1 "$binary" -b 127.0.0.1 -p 15901 -n iPhoneBridge \
        -B off -H 0 -C off -T off -i off -I off \
        -O on -U off -s 1 -F 60 -P 60 -d 0 &
    child=$!
    printf '%s\n' "$child" > "$active/pid"
    # Wait for exec before recording a PID identity; no input is sent here.
    n=0
    while [ "$n" -lt 10 ]; do
        current=$(identity "$child")
        case "$current" in *"$binary -b "*) break;; esac
        kill -0 "$child" 2>/dev/null || exit 48
        sleep 1
        n=$((n + 1))
    done
    case "$current" in *"$binary -b "*) ;; *) exit 49;; esac
    printf '%s\n' "$current" > "$active/identity"
    wait "$child"
    ;;
*) exit 50;;
esac
