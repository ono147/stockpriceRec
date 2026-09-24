#!/bin/sh
cd "$(dirname "$0")" || exit 1
exec python3 -m stockrec update --interval 1d --interval 1m --interval 5m
