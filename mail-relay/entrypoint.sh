#!/bin/sh
set -eu

: "${RELAY_DOMAIN:?RELAY_DOMAIN must be set (the domain this relay sends mail as, e.g. example.com)}"
: "${RELAY_SMARTHOST:?RELAY_SMARTHOST must be set (e.g. [smtp.yourprovider.example]:587)}"

postconf -e "myhostname = ${RELAY_DOMAIN}"
postconf -e "relayhost = ${RELAY_SMARTHOST}"
postconf -e "smtp_sasl_security_options = noanonymous"
postconf -e "smtp_use_tls = yes"

# This container is a pure outbound relay — it must never accept a message for
# local delivery. Two settings are load-bearing here:
#
#   mydestination =   (empty)
#     Postfix's default mydestination includes $myhostname and
#     $mydomain, and resolving those defaults also wants /etc/mailname,
#     which this minimal image does not ship. The practical effect of
#     leaving it at the default is that mail to any recipient at
#     RELAY_DOMAIN (which IS $myhostname here) is delivered to a local
#     mailbox that nothing ever reads, instead of being relayed onward —
#     i.e. every password-reset email to the deployment's own domain
#     silently disappears. Empty means "this host is the final
#     destination for nothing", so everything goes to $relayhost.
#
#   myorigin = $myhostname
#     The domain appended to bare, unqualified local addresses. Its
#     default also reads /etc/mailname, so without this an unqualified
#     sender/recipient cannot be rewritten and the message defers
#     forever. Single-quoted below so the literal string "$myhostname"
#     reaches postconf for Postfix's own config language to expand — a
#     double-quoted form would be expanded by the shell to the empty
#     string first.
postconf -e 'mydestination ='
postconf -e 'myorigin = $myhostname'

# Without this, Postfix logs via syslog, and this minimal image runs no
# syslogd — so the log goes nowhere and `docker logs` is completely silent
# even while every message is failing. Sending it to stdout is what makes
# delivery failures observable through normal Docker tooling.
postconf -e 'maillog_file = /dev/stdout'
# Only the `mail` Docker network (docker-compose.yml's fixed 172.28.238.0/24
# subnet) can relay through this container without SASL auth of its own —
# core-api is the only other service on that network (webapp and the three
# collectors are deliberately not), so this is core-api authenticating
# implicitly by network membership, not every container on the host. This
# container then authenticates onward to the real smarthost using
# RELAY_USERNAME/RELAY_PASSWORD below. Never exposed outside that network
# (no published port in deployment).
postconf -e "mynetworks = 127.0.0.0/8, [::1]/128, 172.28.238.0/24"

if [ -n "${RELAY_USERNAME:-}" ] && [ -n "${RELAY_PASSWORD:-}" ]; then
    # Only enable SASL when credentials actually exist. Setting these
    # unconditionally points smtp_sasl_password_maps at a hash file that is
    # never created, which makes Postfix fail every delivery attempt rather
    # than falling back to unauthenticated relay.
    postconf -e "smtp_sasl_auth_enable = yes"
    postconf -e "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd"
    echo "${RELAY_SMARTHOST} ${RELAY_USERNAME}:${RELAY_PASSWORD}" > /etc/postfix/sasl_passwd
    postmap /etc/postfix/sasl_passwd
    chmod 600 /etc/postfix/sasl_passwd /etc/postfix/sasl_passwd.db
fi

# Debian runs the smtp(8) delivery agent chrooted to /var/spool/postfix (the
# `y` in master.cf's chroot column), and a chrooted resolver can only read
# files that exist INSIDE that chroot. On a stock image /var/spool/postfix/etc
# is empty, so the smtp client cannot resolve any hostname at all and every
# delivery fails with "Host or domain name not found ... status=deferred" —
# including a real public smarthost, not just a Docker container name.
#
# Debian's own /etc/init.d/postfix does this sync on start, but `postfix
# start-fg` (used here, because a container wants a foreground process and has
# no init system) bypasses the init script entirely, so it has to happen here.
# It must run at container start, not at image build time: Docker generates
# /etc/resolv.conf per-container at runtime.
mkdir -p /var/spool/postfix/etc
for conf_file in /etc/localtime /etc/services /etc/resolv.conf /etc/hosts /etc/nsswitch.conf; do
    if [ -f "${conf_file}" ]; then
        cp -pf "${conf_file}" /var/spool/postfix/etc/
    fi
done

exec postfix start-fg
